from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware
from datetime import datetime
import json
import pandas as pd


# ---------------------------------------------------
# CONNECT
# ---------------------------------------------------
def connect_to(chain):
    """Connect to the blockchain specified by 'chain'."""
    if chain == "source":   # AVAX testnet
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"
    elif chain == "destination":  # BSC testnet
        api_url = "https://data-seed-prebsc-1-s1.binance.org:8545/"
    else:
        raise ValueError(f"Unknown chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


# ---------------------------------------------------
# LOAD CONTRACT INFO
# ---------------------------------------------------
def get_contract_info(chain, contract_info_file):
    try:
        with open(contract_info_file, "r") as f:
            contracts = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info\nPlease contact your instructor\n{e}")
        return None

    return contracts.get(chain)


# ---------------------------------------------------
# LOG EVENTS
# ---------------------------------------------------
def log_events(rows, eventfile="bridge_logs.csv"):
    if not rows:
        return
    df = pd.DataFrame(rows, columns=[
        "chain", "token", "recipient", "amount",
        "transactionHash", "address"
    ])
    try:
        pd.read_csv(eventfile)
        df.to_csv(eventfile, mode="a", header=False, index=False)
    except FileNotFoundError:
        df.to_csv(eventfile, mode="w", header=True, index=False)


# ---------------------------------------------------
# REGISTER TOKENS (placeholder)
# ---------------------------------------------------
def register_and_create_tokens(contract_info="contract_info.json"):
    pass


# ---------------------------------------------------
# MAIN SCAN FUNCTION (merged listener.py + bridge.py)
# ---------------------------------------------------
def scan_blocks(chain, contract_info_file="contract_info.json"):

    if chain not in ["source", "destination"]:
        print(f"Invalid chain: {chain}")
        return

    # Load info for both chains
    this_info = get_contract_info(chain, contract_info_file)
    opp_chain = "destination" if chain == "source" else "source"
    opp_info = get_contract_info(opp_chain, contract_info_file)

    if not this_info or not opp_info:
        print("Failed to read contract info")
        return

    # Connections
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

    # Warden key
    opp_key = opp_info["warden_private_key"]
    opp_acct = w3_opp.eth.account.from_key(opp_key)

    # Event & function mapping
    if chain == "source":
        event_obj = this_contract.events.Deposit
        target_fn = opp_contract.functions.wrap
    else:
        event_obj = this_contract.events.Unwrap
        target_fn = opp_contract.functions.withdraw

    # Block range
    latest = w3.eth.block_number
    start_block = max(0, latest - 5)

    # ---------------------------------------------------
    # OLD WEB3 v5 METHOD — createFilter
    # ---------------------------------------------------
    try:
        flt = event_obj.createFilter(fromBlock=start_block, toBlock=latest)
        events = flt.get_all_entries()
    except Exception as e:
        print(f"Failed to get events: {e}")
        events = []

    print(f"[{chain}] Scanned blocks {start_block}-{latest}, {len(events)} events found.")

    # ---------------------------------------------------
    # PROCESS EVENTS
    # ---------------------------------------------------
    for evt in events:

        # Safe extraction of event fields
        token = evt.args.get("token") or evt.args.get("underlying_token")
        recipient = evt.args["to"]
        amount = evt.args["amount"]

        print(f"[{chain}] Event -> token={token}, to={recipient}, amount={amount}")

        # Send bridging tx on opposite chain
        try:
            nonce = w3_opp.eth.get_transaction_count(opp_acct.address, "pending")

            tx = target_fn(
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

            print(f"[{opp_chain}] Called {target_fn.fn_name}, tx: {tx_hash.hex()}")

        except Exception as e:
            print(f"[{opp_chain}] Transaction failed: {e}")
