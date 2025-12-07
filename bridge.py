from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json


def connect_to(chain: str) -> Web3:
    if chain == "source":
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"
    elif chain == "destination":
        api_url = "https://bsc-testnet.publicnode.com"
    else:
        raise ValueError(f"Unknown chain {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_contract_info(chain: str, fn="contract_info.json"):
    try:
        with open(fn) as f:
            return json.load(f).get(chain)
    except:
        print("Failed to read contract info")
        return None


def event_topic0(event_abi):
    name = event_abi["name"]
    types = ",".join(i["type"] for i in event_abi["inputs"])
    signature = f"{name}({types})"
    return Web3.keccak(text=signature).hex()


def scan_blocks(chain: str, contract_info_file="contract_info.json"):

    if chain not in ["source", "destination"]:
        print(f"Invalid chain {chain}")
        return 0

    this_info = get_contract_info(chain, contract_info_file)
    if not this_info:
        print("Missing contract info")
        return 0

    opp_chain = "destination" if chain == "source" else "source"
    opp_info = get_contract_info(opp_chain, contract_info_file)
    if not opp_info:
        print("Missing opposite chain info")
        return 0

    w3 = connect_to(chain)
    w3_opp = connect_to(opp_chain)

    this_contract = w3.eth.contract(
        address=Web3.to_checksum_address(this_info["address"]),
        abi=this_info["abi"]
    )
    opp_contract = w3_opp.eth.contract(
        address=Web3.to_checksum_address(opp_info["address"]),
        abi=opp_info["abi"]
    )

    opp_key = opp_info.get("warden_private_key")
    if not opp_key:
        print(f"No warden key for {opp_chain}")
        return 0

    opp_acct = w3_opp.eth.account.from_key(opp_key)

    # EVENT SELECT
    if chain == "source":
        event_class = this_contract.events.Deposit
        event_name = "Deposit"
        target_fn = opp_contract.functions.wrap
    else:
        event_class = this_contract.events.Unwrap
        event_name = "Unwrap"
        target_fn = opp_contract.functions.withdraw

    topic0 = event_topic0(event_class().abi)

    # BLOCK RANGE
    latest = w3.eth.block_number
    start = max(0, latest - 5)
    print(f"[{chain}] Scanned blocks {start}-{latest}", end="")

    # GET LOGS
    raw_logs = []
    for blk in range(start, latest + 1):
        params = {
            "fromBlock": blk,
            "toBlock": blk,
            "address": Web3.to_checksum_address(this_info["address"]),
            "topics": [topic0],
        }
        try:
            blk_logs = w3.eth.get_logs(params)
            raw_logs.extend(blk_logs)
        except Exception:
            continue

    print(f", {len(raw_logs)} raw logs found.")

    # DECODE EVENTS
    events = []
    for log in raw_logs:
        try:
            evt = event_class().process_log(log)
            events.append(evt)
        except:
            continue

    print(f"[{chain}] {len(events)} {event_name} events decoded.")

    # PROCESS EVENTS
    for evt in events:
        args = evt["args"]

        if chain == "source":
            token = Web3.to_checksum_address(args["token"])
            recipient = Web3.to_checksum_address(args["recipient"])
            amount = int(args["amount"])
        else:
            # flexible arg naming
            token = Web3.to_checksum_address(
                args.get("underlying_token") or
                args.get("_underlying_token") or
                args.get("token")
            )
            recipient = Web3.to_checksum_address(
                args.get("to") or
                args.get("_recipient") or
                args.get("recipient")
            )
            amount = int(
                args.get("amount") or
                args.get("_amount")
            )

        print(f"[{chain}] {event_name} -> token={token}, recipient={recipient}, amount={amount}")

        try:
            nonce = w3_opp.eth.get_transaction_count(opp_acct.address, "pending")
            tx = target_fn(token, recipient, amount).build_transaction({
                "from": opp_acct.address,
                "nonce": nonce,
                "gas": 500000,
                "gasPrice": w3_opp.eth.gas_price,
            })
            signed = w3_opp.eth.account.sign_transaction(tx, opp_key)
            tx_hash = w3_opp.eth.send_raw_transaction(signed.raw_transaction)
            print(f"[{opp_chain}] Sent {target_fn.fn_name} tx: {tx_hash.hex()}")

        except Exception as e:
            print(f"[{opp_chain}] Transaction failed: {e}")

    return 1
