from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json


# -------------------------------
# RPC CONNECTIONS
# -------------------------------
def connect_to(chain: str) -> Web3:
    if chain == "source":                     # AVAX C-chain testnet
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"
    elif chain == "destination":              # BSC testnet
        api_url = "https://bsc-testnet.publicnode.com"
    else:
        raise ValueError(f"Unknown chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


# -------------------------------
# CONTRACT INFO LOADER
# -------------------------------
def get_contract_info(chain: str, contract_info_file: str = "contract_info.json"):
    try:
        with open(contract_info_file, "r") as f:
            contracts = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info\nPlease contact your instructor\n{e}")
        return None

    return contracts.get(chain)


# -------------------------------
# OPTIONAL: REGISTER / CREATE TOKENS
# -------------------------------
def register_and_create_tokens(contract_info_file: str = "contract_info.json",
                              erc20_csv: str = "erc20s.csv"):
    return


# -------------------------------
# CORE: SCAN BLOCKS & BRIDGE EVENTS
# -------------------------------
def scan_blocks(chain: str, contract_info_file: str = "contract_info.json"):

    if chain not in ["source", "destination"]:
        print(f"Invalid chain: {chain}")
        return 0

    # Load contract info
    this_info = get_contract_info(chain, contract_info_file)
    if not this_info:
        print("Failed to read contract info for", chain)
        return 0

    opp_chain = "destination" if chain == "source" else "source"
    opp_info = get_contract_info(opp_chain, contract_info_file)
    if not opp_info:
        print("Failed to read contract info for", opp_chain)
        return 0

    # Connect
    w3 = connect_to(chain)
    w3_opp = connect_to(opp_chain)

    # Contract objects
    this_contract = w3.eth.contract(
        address=Web3.to_checksum_address(this_info["address"]),
        abi=this_info["abi"],
    )

    opp_contract = w3_opp.eth.contract(
        address=Web3.to_checksum_address(opp_info["address"]),
        abi=opp_info["abi"],
    )

    # Opposite chain key / account
    opp_key = opp_info.get("warden_private_key")
    if not opp_key:
        print(f"No warden_private_key found for {opp_chain}")
        return 0

    opp_acct = w3_opp.eth.account.from_key(opp_key)

    # Which event and which function
    if chain == "source":
        event_name = "Deposit"
        target_fn = opp_contract.functions.wrap
    else:
        event_name = "Unwrap"
        target_fn = opp_contract.functions.withdraw

    # Block window (20 blocks)
    latest = w3.eth.block_number
    start_block = max(0, latest - 20)

    print(f"[{chain}] Scanned blocks {start_block}-{latest}", end="")

    # Collect raw logs
    logs = []
    for blk in range(start_block, latest + 1):
        filter_params = {
            "fromBlock": blk,
            "toBlock": blk,
            "address": Web3.to_checksum_address(this_info["address"]),
        }
        try:
            blk_logs = w3.eth.get_logs(filter_params)
            logs.extend(blk_logs)
        except Exception as e:
            print(f"\n[{chain}] Warning: get_logs failed for block {blk}: {e}")
            continue

    print(f", {len(logs)} raw logs found.")

    # Decode logs
    events = []
    for log in logs:
        try:
            if chain == "source":
                evt = this_contract.events.Deposit().process_log(log)
            else:
                evt = this_contract.events.Unwrap().process_log(log)
            events.append(evt)
        except Exception:
            continue

    print(f"[{chain}] {len(events)} {event_name} events decoded.")

    # ------------------------------------
    # ⭐ FIX: get nonce once, increment manually
    # ------------------------------------
    nonce = w3_opp.eth.get_transaction_count(opp_acct.address, "pending")

    # Process events
    for evt in events:
        args = evt["args"]

        if chain == "source":
            token = Web3.to_checksum_address(args["token"])
            recipient = Web3.to_checksum_address(args["recipient"])
            amount = int(args["amount"])
        else:
            token = Web3.to_checksum_address(args["underlying_token"])
            recipient = Web3.to_checksum_address(args["to"])
            amount = int(args["amount"])

        print(f"[{chain}] {event_name} -> token={token}, recipient={recipient}, amount={amount}")

        try:
            # Build tx using CURRENT nonce
            tx = target_fn(token, recipient, amount).build_transaction({
                "from": opp_acct.address,
                "nonce": nonce,
                "gas": 500_000,
                "gasPrice": w3_opp.eth.gas_price,
            })

            signed = w3_opp.eth.account.sign_transaction(tx, opp_key)
            tx_hash = w3_opp.eth.send_raw_transaction(signed.raw_transaction)
            print(f"[{opp_chain}] Sent {target_fn.fn_name} tx: {tx_hash.hex()}")

            # ⭐ increment the nonce so next tx is not a replacement
            nonce += 1

        except Exception as e:
            print(f"[{opp_chain}] Transaction failed: {e}")

    return 1


# Manual test
if __name__ == "__main__":
    scan_blocks("source")
    scan_blocks("destination")
