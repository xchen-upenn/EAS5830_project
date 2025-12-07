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

    if chain not in ["source", "destination"]:
        print(f"Invalid chain: {chain}")
        return

    # Load contract info
    this_info = get_contract_info(chain, contract_info_file)
    opp_chain = "destination" if chain == "source" else "source"
    opp_info = get_contract_info(opp_chain, contract_info_file)

    if not this_info or not opp_info:
        print("Failed to read contract info")
        return

    # Connect
    w3 = connect_to(chain)
    w3_opp = connect_to(opp_chain)

    # Contracts
    this_contract = w3.eth.contract(
        address=Web3.to_checksum_address(this_info["address"]),
        abi=this_info["abi"]
    )

    opp_contract = w3_opp.eth.contract(
        address=Web3.to_checksum_address(opp_info["address"]),
        abi=opp_info["abi"]
    )

    # Opposite chain key/account
    opp_key = opp_info.get("warden_private_key")
    opp_acct = w3_opp.eth.account.from_key(opp_key)

    # Which event is emitted?
    if chain == "source":
        event_obj = this_contract.events.Deposit
        target_function = opp_contract.functions.wrap
    else:
        event_obj = this_contract.events.Unwrap
        target_function = opp_contract.functions.withdraw

    # Choose block range
    latest = w3.eth.block_number
    start_block = max(0, latest - 5)

    # FIX: MUST use dict with camelCase fields
    try:
        logs = event_obj.get_logs({
            "fromBlock": start_block,
            "toBlock": latest
        })
    except Exception as e:
        print(f"Failed to get events: {e}")
        logs = []

    print(f"[{chain}] Scanned blocks {start_block}-{latest}, {len(logs)} events found.")

    # Process events
    for evt in logs:
        args = evt.args

        # Flexible extraction based on the ABI
        keys = list(args.keys())

        token     = args.get("token") or args.get("_token") or args.get("underlying_token") or args.get("_underlying_token") or args[keys[0]]
        recipient = args.get("to") or args.get("_to") or args.get("recipient") or args[keys[1]]
        amount    = args.get("amount") or args.get("_amount") or args[keys[2]]

        print(f"[{chain}] Event: token={token}, to={recipient}, amount={amount}")

        # Build and send transaction on opposite chain
        try:
            nonce = w3_opp.eth.get_transaction_count(opp_acct.address, "pending")

            tx = target_function(
                Web3.to_checksum_address(token),
                Web3.to_checksum_address(recipient),
                int(amount)
            ).build_transaction({
                "from": opp_acct.address,
                "nonce": nonce,
                "gas": 500000,
                "gasPrice": w3_opp.eth.gas_price
            })

            signed = w3_opp.eth.account.sign_transaction(tx, opp_key)
            tx_hash = w3_opp.eth.send_raw_transaction(signed.rawTransaction)

            print(f"[{opp_chain}] Sent {target_function.fn_name}, tx hash: {tx_hash.hex()}")

        except Exception as e:
            print(f"[{opp_chain}] Transaction failed: {e}")

