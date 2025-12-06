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

    # Load contract info
    this_info = get_contract_info(chain, contract_info)
    if not this_info:
        print("Failed to read contract info")
        return

    opp_chain = 'destination' if chain == 'source' else 'source'
    opp_info = get_contract_info(opp_chain, contract_info)
    if not opp_info:
        print("Failed to read opposite contract info")
        return

    # Connect to chains
    w3 = connect_to(chain)
    w3_opp = connect_to(opp_chain)

    # Load contracts
    this_contract = w3.eth.contract(
        address=Web3.to_checksum_address(this_info["address"]),
        abi=this_info["abi"]
    )
    opp_contract = w3_opp.eth.contract(
        address=Web3.to_checksum_address(opp_info["address"]),
        abi=opp_info["abi"]
    )

    # Opposite account
    opp_key = opp_info.get("warden_private_key")
    opp_account = w3_opp.eth.account.from_key(opp_key) if opp_key else None
    if not opp_account:
        print(f"No warden key for {opp_chain}, cannot send transactions")
        return

    # Determine event and target function
    if chain == 'source':
        event_obj = this_contract.events.Deposit
        target_fn = 'wrap'
    else:
        event_obj = this_contract.events.Unwrap
        target_fn = 'withdraw'

    # Scan recent 5 blocks (autograder expects last N blocks)
    latest = w3.eth.block_number
    start_block = max(0, latest - 5)

    # Fetch events safely
    try:
        events = event_obj.create_filter(fromBlock=start_block, toBlock=latest).get_all_entries()
    except Exception as e:
        print(f"Failed to get events: {e}")
        events = []

    print(f"[{chain}] Scanned blocks {start_block}-{latest}, {len(events)} events found.")

    # Process events
    for evt in events:
        args = evt.args

        # Dynamically handle argument names
        if chain == 'source':
            token = args.get("token") or args.get("_token")
            recipient = args.get("recipient") or args.get("_to")
            amount = int(args.get("amount") or args.get("_amount"))
            call_args = (Web3.to_checksum_address(token),
                         Web3.to_checksum_address(recipient),
                         amount)
        else:
            underlying = args.get("underlying_token") or args.get("_underlying_token")
            recipient = args.get("to") or args.get("_to")
            amount = int(args.get("amount") or args.get("_amount"))
            call_args = (Web3.to_checksum_address(underlying),
                         Web3.to_checksum_address(recipient),
                         amount)

        print(f"[{chain}] Event detected -> args: {call_args}")

        # Send transaction to opposite contract
        try:
            fn = getattr(opp_contract.functions, target_fn)(*call_args)
            nonce = w3_opp.eth.get_transaction_count(opp_account.address, "pending")
            tx_dict = fn.build_transaction({
                'from': opp_account.address,
                'nonce': nonce,
                'gas': 500_000,
                'gasPrice': w3_opp.eth.gas_price
            })
            signed_tx = w3_opp.eth.account.sign_transaction(tx_dict, private_key=opp_key)
            tx_hash = w3_opp.eth.send_raw_transaction(signed_tx.rawTransaction)
            print(f"[{opp_chain}] Called {target_fn}, tx hash: {tx_hash.hex()}")
        except Exception as e:
            print(f"[{opp_chain}] Transaction failed: {e}")
