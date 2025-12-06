from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware  # Necessary for POA chains
from datetime import datetime
import json
import pandas as pd


def connect_to(chain):
    """Connect to the blockchain specified by 'chain'."""
    if chain == 'source':  # The source contract chain is AVAX
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"  # AVAX C-chain testnet

    elif chain == 'destination':  # The destination contract chain is BSC
        api_url = "https://data-seed-prebsc-1-s1.binance.org:8545/"  # BSC testnet

    else:
        raise ValueError(f"Unknown chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    # Inject the POA compatibility middleware to the innermost layer
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    return w3


def get_contract_info(chain, contract_info):
    """
    Load the contract_info file into a dictionary.

    This function is used by the autograder and will likely be useful to you.
    """
    try:
        with open(contract_info, 'r') as f:
            contracts = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info\nPlease contact your instructor\n{e}")
        return None

    return contracts.get(chain)


# -------------------------------
# Event logging helper
# -------------------------------
def log_events(rows, eventfile="bridge_logs.csv"):
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
# Register & create tokens (placeholder)
# -------------------------------
def register_and_create_tokens(contract_info="contract_info.json"):
    """
    This function can register tokens on source/destination contracts and create wrapped tokens.
    Autograder may call it, or it can be left empty if tokens already exist.
    """
    pass  # Implementation depends on assignment requirements

# -------------------------------
# SCAN BLOCKS & BRIDGE EVENTS
# -------------------------------
def scan_blocks(chain, contract_info_file="contract_info.json"):
    """Scan last few blocks on a chain, detect Deposit/Unwrap events, and trigger wrap/withdraw on opposite chain."""
    if chain not in ['source', 'destination']:
        print(f"Invalid chain: {chain}")
        return

    # Connect to this chain
    w3 = connect_to(chain)
    this_info = get_contract_info(contract_info_file, chain)
    this_contract = w3.eth.contract(
        address=Web3.to_checksum_address(this_info["address"]),
        abi=this_info["abi"]
    )

    # Connect to opposite chain
    opp_chain = 'destination' if chain == 'source' else 'source'
    w3_opp = connect_to(opp_chain)
    opp_info = get_contract_info(contract_info_file, opp_chain)
    opp_contract = w3_opp.eth.contract(
        address=Web3.to_checksum_address(opp_info["address"]),
        abi=opp_info["abi"]
    )

    # Opposite account for sending transactions
    opp_key = opp_info.get("warden_private_key")
    opp_account = w3_opp.eth.account.from_key(opp_key) if opp_key else None

    # Determine events & target functions
    if chain == 'source':
        event_name = "Deposit"
        target_fn = "wrap"
    else:
        event_name = "Unwrap"
        target_fn = "withdraw"

    event_obj = getattr(this_contract.events, event_name)

    # Scan last 5 blocks
    latest_block = w3.eth.block_number
    start_block = max(0, latest_block - 5)

    try:
        events = event_obj.get_logs({"fromBlock": start_block, "toBlock": latest_block})
    except Exception as e:
        print(f"Failed to get events: {e}")
        events = []

    for evt in events:
        args = evt.args
        if chain == 'source':
            token = Web3.to_checksum_address(args["token"])
            recipient = Web3.to_checksum_address(args["recipient"])
            amount = int(args["amount"])
            call_args = (token, recipient, amount)
        else:
            underlying = Web3.to_checksum_address(args["underlying_token"])
            recipient = Web3.to_checksum_address(args["to"])
            amount = int(args["amount"])
            call_args = (underlying, recipient, amount)

        print(f"[{chain}] {event_name} event -> args: {call_args}")

        # Trigger opposite contract
        if opp_account:
            fn = getattr(opp_contract.functions, target_fn)(*call_args)
            nonce = w3_opp.eth.get_transaction_count(opp_account.address, "pending")
            tx = fn.build_transaction({
                "from": opp_account.address,
                "nonce": nonce,
                "gas": 400_000,
                "gasPrice": w3_opp.eth.gas_price
            })
            signed = w3_opp.eth.account.sign_transaction(tx, opp_key)
            tx_hash = w3_opp.eth.send_raw_transaction(signed.rawTransaction)
            print(f"[{opp_chain}] Called {target_fn} -> tx: {tx_hash.hex()}")

    print(f"[{chain}] Scanned blocks {start_block}-{latest_block}, {len(events)} events processed.")
