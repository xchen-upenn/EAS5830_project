from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json


def connect_to(chain: str) -> Web3:
    if chain == "source":
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"
    elif chain == "destination":
        api_url = "https://bsc-testnet.publicnode.com"
    else:
        raise ValueError(f"Unknown chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_contract_info(chain: str, contract_info_file: str = "contract_info.json"):
    try:
        with open(contract_info_file, "r") as f:
            contracts = json.load(f)
        return contracts.get(chain)
    except Exception as e:
        print("Failed to read contract info\n", e)
        return None


def register_and_create_tokens(*args, **kwargs):
    return


def scan_blocks(chain: str, contract_info_file: str = "contract_info.json"):

    if chain not in ["source", "destination"]:
        print(f"Invalid chain: {chain}")
        return 0

    this_info = get_contract_info(chain, contract_info_file)
    opp_chain = "destination" if chain == "source" else "source"
    opp_info = get_contract_info(opp_chain, contract_info_file)

    if not this_info or not opp_info:
        print("Missing contract info")
        return 0

    w3 = connect_to(chain)
    w3_opp = connect_to(opp_chain)

    this_contract = w3.eth.contract(
        address=Web3.to_checksum_address(this_info["address"]),
        abi=this_info["abi"],
    )
    opp_contract = w3_opp.eth.contract(
        address=Web3.to_checksum_address(opp_info["address"]),
        abi=opp_info["abi"],
    )

    opp_key = opp_info.get("warden_private_key")
    if not opp_key:
        print(f"No warden_private_key for {opp_chain}")
        return 0

    opp_acct = w3_opp.eth.account.from_key(opp_key)

    # Which event to decode and which function to call?
    if chain == "source":
        event_name = "Deposit"
        target_fn = opp_contract.functions.wrap
    else:
        event_name = "Unwrap"
        target_fn = opp_contract.functions.withdraw

    latest = w3.eth.block_number
    start_block = max(0, latest - 5)
    print(f"[{chain}] Scanned blocks {start_block}-{latest}", end="")

    logs = []
    for blk in range(start_block, latest + 1):
        params = {
            "fromBlock": blk,
            "toBlock": blk,
            "address": Web3.to_checksum_address(this_info["address"]),
        }
        try:
            blk_logs = w3.eth.get_logs(params)
            logs.extend(blk_logs)
        except Exception as e:
            print(f"\n[{chain}] Warning block {blk}: {e}")
            continue

    print(f", {len(logs)} raw logs found.")

    # Decode events
    events = []
    for log in logs:
        try:
            if chain == "source":
                evt = this_contract.events.Deposit().process_log(log)
            else:
                evt = this_contract.events.Unwrap().process_log(log)
            events.append(evt)
        except:
            continue

    print(f"[{chain}] {len(events)} {event_name} events decoded.")

    # Process the events
    for evt in events:
        args = evt["args"]

        if chain == "source":
            # Deposit(token, recipient, amount)
            token = Web3.to_checksum_address(args["token"])
            recipient = Web3.to_checksum_address(args["recipient"])
            amount = int(args["amount"])

        else:
            # Unwrap(underlying_token, wrapped_token, frm, to, amount)
            # ❗ FIX: must call withdraw(wrapped_token, ...)
            wrapped_token = Web3.to_checksum_address(args["wrapped_token"])
            recipient = Web3.to_checksum_address(args["to"])
            amount = int(args["amount"])

        if chain == "source":
            print(f"[{chain}] Deposit -> token={token}, recipient={recipient}, amount={amount}")
        else:
            print(f"[{chain}] Unwrap -> wrapped={wrapped_token}, recipient={recipient}, amount={amount}")

        try:
            nonce = w3_opp.eth.get_transaction_count(opp_acct.address, "pending")

            # Correct transaction based on chain direction
            if chain == "source":
                tx = target_fn(token, recipient, amount).build_transaction({
                    "from": opp_acct.address,
                    "nonce": nonce,
                    "gas": 500_000,
                    "gasPrice": w3_opp.eth.gas_price,
                })
            else:
                # ❗ FIX: withdraw(wrapped_token, recipient, amount)
                tx = target_fn(wrapped_token, recipient, amount).build_transaction({
                    "from": opp_acct.address,
                    "nonce": nonce,
                    "gas": 500_000,
                    "gasPrice": w3_opp.eth.gas_price,
                })

            signed = w3_opp.eth.account.sign_transaction(tx, opp_key)
            tx_hash = w3_opp.eth.send_raw_transaction(signed.raw_transaction)
            print(f"[{opp_chain}] Sent {target_fn.fn_name} tx: {tx_hash.hex()}")

        except Exception as e:
            print(f"[{opp_chain}] Transaction failed: {e}")

    return 1


if __name__ == "__main__":
    scan_blocks("source")
    scan_blocks("destination")
