from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware
import json
import pandas as pd


# ---------------------------------------------------------
# CONNECT
# ---------------------------------------------------------
def connect_to(chain):
    if chain == "source":
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"
    elif chain == "destination":
        api_url = "https://data-seed-prebsc-1-s1.binance.org:8545/"
    else:
        raise ValueError("Unknown chain")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


# ---------------------------------------------------------
# READ CONTRACT INFO
# ---------------------------------------------------------
def get_contract_info(chain, file):
    try:
        with open(file, "r") as f:
            info = json.load(f)
        return info.get(chain)
    except Exception as e:
        print("Failed to read contract info\nPlease contact your instructor")
        print(e)
        return None


# ---------------------------------------------------------
# LOG EVENTS (OPTIONAL)
# ---------------------------------------------------------
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


# ---------------------------------------------------------
# SCAN BLOCKS (MAIN FUNCTION)
# ---------------------------------------------------------
def scan_blocks(chain, contract_info_file="contract_info.json"):

    if chain not in ["source", "destination"]:
        print("Invalid chain")
        return

    # Load contracts
    this_info = get_contract_info(chain, contract_info_file)
    if not this_info:
        print("Failed to read contract info")
        return

    opp_chain = "destination" if chain == "source" else "source"
    opp_info = get_contract_info(opp_chain, contract_info_file)
    if not opp_info:
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

    # Event choosing
    if chain == "source":
        event_abi = this_contract.events.Deposit().abi
        target_fn = opp_contract.functions.wrap
    else:
        event_abi = this_contract.events.Unwrap().abi
        target_fn = opp_contract.functions.withdraw

    # Block range
    latest = w3.eth.block_number
    start_block = max(0, latest - 5)

    # ---------------------------------------------------------
    # OLD WEB3 METHOD: CALL w3.eth.get_logs() DIRECTLY
    # ---------------------------------------------------------
    filter_params = {
        "fromBlock": start_block,
        "toBlock": latest,
        "address": Web3.to_checksum_address(this_info["address"])
    }

    try:
        logs = w3.eth.get_logs(filter_params)
    except Exception as e:
        print(f"Failed to get events: {e}")
        logs = []

    print(f"[{chain}] Scanned blocks {start_block}-{latest}, {len(logs)} events found.")

    # ---------------------------------------------------------
    # DECODE LOGS MANUALLY
    # ---------------------------------------------------------
    for log in logs:
        try:
            evt = this_contract.events.Deposit().process_log(log) \
                if chain == "source" else \
                this_contract.events.Unwrap().process_log(log)
        except:
            continue  # skip logs not matching event

        token = evt.args.get("token") or evt.args.get("underlying_token")
        recipient = evt.args["to"]
        amount = evt.args["amount"]

        print(f"[{chain}] Event token={token}, to={recipient}, amount={amount}")

        # Send bridging tx
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

            print(f"[{opp_chain}] Sent {target_fn.fn_name} tx={tx_hash.hex()}")

        except Exception as e:
            print(f"[{opp_chain}] Transaction failed: {e}")
