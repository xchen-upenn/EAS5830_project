from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json
import pandas as pd

# -------------------------------
# Utility to connect to chains
# -------------------------------
def connect_to(chain):
    if chain == 'source':
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"  # AVAX C-chain
    elif chain == 'destination':
        api_url = "https://bsc-testnet.publicnode.com"  # BSC testnet
    else:
        raise ValueError(f"Unknown chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3

# -------------------------------
# Load contract info and accounts
# -------------------------------
with open("contract_info.json", "r") as f:
    contracts = json.load(f)

source_info = contracts['source']
dest_info = contracts['destination']

w3_source = connect_to('source')
w3_dest = connect_to('destination')

source_contract = w3_source.eth.contract(
    address=Web3.to_checksum_address(source_info["address"]),
    abi=source_info["abi"]
)
dest_contract = w3_dest.eth.contract(
    address=Web3.to_checksum_address(dest_info["address"]),
    abi=dest_info["abi"]
)

source_account = w3_source.eth.account.from_key(source_info["warden_private_key"])
dest_account = w3_dest.eth.account.from_key(dest_info["warden_private_key"])

# -------------------------------
# Event logging helper
# -------------------------------
def log_events(rows, eventfile):
    if not rows:
        return
    df = pd.DataFrame(rows, columns=[
        "chain", "token", "recipient", "amount", "transactionHash", "address"
    ])
    try:
        pd.read_csv(eventfile)  # check existence
        df.to_csv(eventfile, mode="a", header=False, index=False)
    except FileNotFoundError:
        df.to_csv(eventfile, mode="w", header=True, index=False)

# -------------------------------
# Scan blocks and bridge tokens
# -------------------------------
def scan_blocks(chain, start_block, end_block, eventfile='bridge_logs.csv'):
    """
    chain: 'source' or 'destination'
    start_block, end_block: block numbers to scan
    eventfile: CSV file to log events
    """
    if chain == 'source':
        w3 = w3_source
        this_contract = source_contract
        opp_contract = dest_contract
        opp_account = dest_account
        event_name = "Deposit"
        target_fn = "wrap"
    else:
        w3 = w3_dest
        this_contract = dest_contract
        opp_contract = source_contract
        opp_account = source_account
        event_name = "Unwrap"
        target_fn = "withdraw"

    # Determine latest block if needed
    if start_block == "latest":
        start_block = w3.eth.block_number
    if end_block == "latest":
        end_block = w3.eth.block_number

    print(f"[{chain}] scanning blocks {start_block} - {end_block}")

    # Setup event filter
    event_abi = None
    for item in this_contract.abi:
        if item.get("type") == "event" and item.get("name") == event_name:
            event_abi = item
            break
    if not event_abi:
        print(f"No ABI for event {event_name} found!")
        return

    event_obj = getattr(this_contract.events, event_name)

    rows = []

    for block_num in range(start_block, end_block + 1):
        event_filter = event_obj.create_filter(from_block=block_num, to_block=block_num)
        events = event_filter.get_all_entries()
        for evt in events:
            # Extract common fields
            if chain == 'source':
                token = evt.args["token"]
                recipient = evt.args["recipient"]
                amount = int(evt.args["amount"])
                call_args = (Web3.to_checksum_address(token),
                             Web3.to_checksum_address(recipient),
                             amount)
            else:
                underlying_token = evt.args["underlying_token"]
                recipient = evt.args["to"]
                amount = int(evt.args["amount"])
                call_args = (Web3.to_checksum_address(underlying_token),
                             Web3.to_checksum_address(recipient),
                             amount)

            print(f"[{chain}] {event_name} event -> args: {call_args}")

            # Log the event
            rows.append({
                "chain": chain,
                "token": token if chain=='source' else underlying_token,
                "recipient": recipient,
                "amount": amount,
                "transactionHash": evt.transactionHash.hex(),
                "address": evt.address
            })

            # Build and send transaction to opposite contract
            fn = getattr(opp_contract.functions, target_fn)(*call_args)
            nonce = w3.eth.get_transaction_count(opp_account.address)
            tx_dict = fn.build_transaction({
                'from': opp_account.address,
                'nonce': nonce,
                'gas': 300000,
                'gasPrice': w3.eth.gas_price
            })
            signed_tx = w3.eth.account.sign_transaction(tx_dict,
                                                        private_key=opp_account.key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            print(f"[{chain}] Called {target_fn} on opposite chain, tx hash: {tx_hash.hex()}")

    # Save logs
    log_events(rows, eventfile)
    print(f"[{chain}] Done processing blocks {start_block}-{end_block}")

# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    # Scan last 5 blocks on both chains
    latest_source = w3_source.eth.block_number
    latest_dest = w3_dest.eth.block_number

    scan_blocks('source', latest_source-5, latest_source)
    scan_blocks('destination', latest_dest-5, latest_dest)
