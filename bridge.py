from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json


# -----------------------------------------
# RPC CONNECTIONS
# -----------------------------------------
def connect_to(chain: str) -> Web3:
    if chain == "source":     # AVAX Fuji C-chain
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"
    elif chain == "destination":  # BSC testnet
        api_url = "https://bsc-testnet.publicnode.com"
    else:
        raise ValueError(f"Unknown chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


# -----------------------------------------
# CONTRACT INFO LOADER
# -----------------------------------------
def get_contract_info(chain: str, filename="contract_info.json"):
    try:
        with open(filename, "r") as f:
            contracts = json.load(f)
        return contracts.get(chain)
    except Exception as e:
        print("Failed to read contract info\nPlease contact your instructor\n", e)
        return None


# -----------------------------------------
# TOKEN REGISTRATION PLACEHOLDER
# -----------------------------------------
def register_and_create_tokens(*args, **kwargs):
    return


# -----------------------------------------
# CORE: SCAN BLOCKS
# -----------------------------------------
def scan_blocks(chain: str, contract_info_file="contract_info.json"):

    if chain not in ["source", "destination"]:
        print(f"Invalid chain {chain}")
        return 0

    # Load both contract configs
    this_info = get_contract_info(chain, contract_info_file)
    if not this_info:
        print(f"Failed to read contract info for {chain}")
        return 0

    opp_chain = "destination" if chain == "source" else "source"
    opp_info = get_contract_info(opp_chain, contract_info_file)
    if not opp_info:
        print(f"Failed to read contract info for {opp_chain}")
        return 0

    # Connect RPC
    w3 = connect_to(chain)
    w3_opp = connect_to(opp_chain)

    # Contract objects
    this_contract = w3.eth.contract(
        address=Web3.to_checksum_address(this_info["address"]),
        abi=this_info["abi"]
    )
    opp_contract = w3_opp.eth.contract(
        address=Web3.to_checksum_address(opp_info["address"]),
        abi=opp_info["abi"]
    )

    # Opposite chain private key
    opp_key = opp_info.get("warden_private_key")
    if not opp_key:
        print(f"No warden_private_key for {opp_chain}")
        return 0

    opp_acct = w3_opp.eth.account.from_key(opp_key)

    # -------------------------------
    # Event selection
    # -------------------------------
    if chain == "source":
        event_class = this_contract.events.Deposit
        sig = event_class().abi["signature"]   # old-web3 format
        target_fn = opp_contract.functions.wrap
        event_name = "Deposit"
    else:
        event_class = this_contract.events.Unwrap
        sig = event_class().abi["signature"]
        target_fn = opp_contract.functions.withdraw
        event_name = "Unwrap"

    # MUST keccak hash the signature → topic0
    topic0 = Web3.keccak(text=sig).hex()

    # -------------------------------
    # Block range (last 5)
    # -------------------------------
    latest = w3.eth.block_number
    start_block = max(0, latest - 5)

    print(f"[{chain}] Scanning blocks {start_block}-{latest}")

    # -------------------------------
    # Manual per-block log fetch
    # -------------------------------
    raw_logs = []
    for blk in range(start_block, latest + 1):
        params = {
            "fromBlock": blk,
            "toBlock": blk,
            "address": Web3.to_checksum_address(this_info["address"]),
            "topics": [topic0]
        }
        try:
            logs = w3.eth.get_logs(params)
            raw_logs.extend(logs)
        except Exception as e:
            print(f"[{chain}] Warning block {blk}: {e}")

    print(f"[{chain}] {len(raw_logs)} raw logs found.")

    # -------------------------------
    # Decode events
    # -------------------------------
    decoded = []
    for log in raw_logs:
        try:
            evt = event_class().process_log(log)
            decoded.append(evt)
        except:
            continue

    print(f"[{chain}] {len(decoded)} {event_name} events decoded.")

    # -------------------------------
    # Process each event
    # -------------------------------
    for evt in decoded:
        args = evt["args"]

        if chain == "source":
            token = Web3.to_checksum_address(args["token"])
            recipient = Web3.to_checksum_address(args["recipient"])
            amount = int(args["amount"])
        else:
            token = Web3.to_checksum_address(args["underlying_token"])
            recipient = Web3.to_checksum_address(args["to"])
            amount = int(args["amount"])

        print(f"[{chain}] {event_name}: token={token}, recipient={recipient}, amount={amount}")

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


# Manual test
if __name__ == "__main__":
    scan_blocks("source")
    scan_blocks("destination")
