from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware
from datetime import datetime
import json
import pandas as pd

def connect_to(chain):
    if chain == 'source':  # AVAX C-chain testnet
        api_url = f"https://api.avax-test.network/ext/bc/C/rpc"
    elif chain == 'destination':  # BSC testnet
        api_url = f"https://bsc-testnet.publicnode.com"
    else:
        raise ValueError(f"Unknown chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_contract_info(chain, contract_info_file="contract_info.json"):
    with open(contract_info_file, 'r') as f:
        contracts = json.load(f)
    return contracts[chain]


def register_and_create_tokens(contract_info_file="contract_info.json", erc20_csv="erc20s.csv"):
    # Load tokens
    df = pd.read_csv(erc20_csv)
    token_addresses = df['tokenID'].tolist()

    # --- SOURCE CONTRACT: register tokens ---
    w3_source = connect_to('source')
    source_info = get_contract_info('source', contract_info_file)
    source_contract = w3_source.eth.contract(
        address=Web3.to_checksum_address(source_info["address"]),
        abi=source_info["abi"]
    )
    source_key = source_info.get("warden_private_key")
    source_account = w3_source.eth.account.from_key(source_key)
    source_nonce = w3_source.eth.get_transaction_count(source_account.address)

    for i, token in enumerate(token_addresses):
        token = Web3.to_checksum_address(token)
        fn = source_contract.functions.registerToken(token)
        tx_dict = fn.build_transaction({
            'from': source_account.address,
            'nonce': source_nonce + i,
            'gas': 200000,
            'gasPrice': w3_source.eth.gas_price
        })
        signed = w3_source.eth.account.sign_transaction(tx_dict, private_key=source_key)
        tx_hash = w3_source.eth.send_raw_transaction(signed.rawTransaction)
        print(f"[SOURCE] Registered token {token}, tx hash: {tx_hash.hex()}")
    
    # --- DESTINATION CONTRACT: create tokens ---
    w3_dest = connect_to('destination')
    dest_info = get_contract_info('destination', contract_info_file)
    dest_contract = w3_dest.eth.contract(
        address=Web3.to_checksum_address(dest_info["address"]),
        abi=dest_info["abi"]
    )
    dest_key = dest_info.get("warden_private_key")
    dest_account = w3_dest.eth.account.from_key(dest_key)
    dest_nonce = w3_dest.eth.get_transaction_count(dest_account.address)

    for i, token in enumerate(token_addresses):
        token = Web3.to_checksum_address(token)
        # Provide token name and symbol if needed
        name = f"Token{i+1}"
        symbol = f"T{i+1}"
        fn = dest_contract.functions.createToken(token, name, symbol)
        tx_dict = fn.build_transaction({
            'from': dest_account.address,
            'nonce': dest_nonce + i,
            'gas': 300000,
            'gasPrice': w3_dest.eth.gas_price
        })
        signed = w3_dest.eth.account.sign_transaction(tx_dict, private_key=dest_key)
        tx_hash = w3_dest.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[DEST] Created token {token}, tx hash: {tx_hash.hex()}")


# Call this once at the start of your bridge.py
register_and_create_tokens()

# --------------------------
# Rest of your scan_blocks() code remains unchanged
# --------------------------
def scan_blocks(chain, contract_info="contract_info.json"):
    """
        chain - (string) should be either "source" or "destination"
        Scan the last 5 blocks of the source and destination chains
        Look for 'Deposit' events on the source chain and 'Unwrap' events on the destination chain
        When Deposit events are found on the source chain, call the 'wrap' function the destination chain
        When Unwrap events are found on the destination chain, call the 'withdraw' function on the source chain
    """

    # This is different from Bridge IV where chain was "avax" or "bsc"
    if chain not in ['source','destination']:
        print( f"Invalid chain: {chain}" )
        return 0
    
        #YOUR CODE HERE
    # 1. Connect to the chain being scanned
    w3 = connect_to(chain)

    # 2. Load contract info for this chain and the opposite chain
    try:
        this_info = get_contract_info(chain, contract_info)
    except Exception as e:
        print("Error loading contract info:", e)
        return 0

    opp_chain = 'destination' if chain == 'source' else 'source'
    try:
        opp_info = get_contract_info(opp_chain, contract_info)
    except Exception as e:
        print("Error loading opposite contract info:", e)
        return 0

    # 3. Prepare contracts
    try:
        this_addr = Web3.to_checksum_address(this_info["address"])
        this_abi = this_info["abi"]
    except Exception as e:
        print("Missing address/abi in contract_info for", chain, e)
        return 0

    try:
        opp_addr = Web3.to_checksum_address(opp_info["address"])
        opp_abi = opp_info["abi"]
    except Exception as e:
        print("Missing address/abi in contract_info for", opp_chain, e)
        return 0

    this_contract = w3.eth.contract(address=this_addr, abi=this_abi)

    # 4. Decide which event to watch and how to call the opposite contract
    if chain == 'source':
        # source emits Deposit(token, recipient, amount)
        event_obj = this_contract.events.Deposit
        # We'll call destination.wrap(token, recipient, amount)
        target_fn_name = "wrap"
        # On destination chain we'll use its web3 and contract
        w3_opp = connect_to('destination')
    else:
        # destination emits Unwrap(underlying_token, wrapped_token, frm, to, amount)
        event_obj = this_contract.events.Unwrap
        # We'll call source.withdraw(underlying_token, to, amount)
        target_fn_name = "withdraw"
        w3_opp = connect_to('source')

    opp_contract = w3_opp.eth.contract(address=opp_addr, abi=opp_abi)

    # 5. Determine block range (last 5 blocks)
    try:
        latest = w3.eth.block_number
    except Exception as e:
        print("Unable to get block number from chain", chain, e)
        return 0

    start_block = max(0, latest - 5)
    end_block = latest
    print(f"[{chain}] scanning blocks {start_block} - {end_block}")

    # 6. Create filter & fetch events
    try:
        event_filter = event_obj.create_filter(fromBlock=start_block, toBlock=end_block)
        events = event_filter.get_all_entries()
    except Exception as e:
        print("Failed to create event filter / fetch events:", e)
        return 0

    if not events:
        print(f"[{chain}] No events found in block range.")
        return 1

    # 7. Prepare account from opp_info (to send tx on the opposite chain)
    opp_private_key = opp_info.get("warden_private_key", None)
    if opp_private_key:
        try:
            opp_account = w3_opp.eth.account.from_key(opp_private_key)
            opp_address = opp_account.address
            print(f"[{opp_chain}] will send transactions from {opp_address}")
        except Exception as e:
            print("Invalid opp private key:", e)
            opp_private_key = None
            opp_address = None
    else:
        print(f"Warning: No warden_private_key found for {opp_chain} in {contract_info}."
              " The script will not broadcast transactions but will print what it would do.")
        opp_address = None

    # 8. Process each event and call the opposite contract
    for evt in events:
        try:
            args = evt.args
        except Exception:
            # older web3 versions might store event data differently
            args = evt['args'] if 'args' in evt else {}

        # Extract values depending on the event type
        if chain == 'source':
            # Deposit(token, recipient, amount)
            token = args.get("token")
            recipient = args.get("recipient")
            amount = int(args.get("amount"))
            call_args = (Web3.to_checksum_address(token), Web3.to_checksum_address(recipient), amount)
            print(f"[{chain}] Deposit event -> token={token}, recipient={recipient}, amount={amount}")
        else:
            # Unwrap(underlying_token, wrapped_token, frm, to, amount)
            underlying_token = args.get("underlying_token")
            recipient = args.get("to")
            amount = int(args.get("amount"))
            call_args = (Web3.to_checksum_address(underlying_token), Web3.to_checksum_address(recipient), amount)
            print(f"[{chain}] Unwrap event -> underlying_token={underlying_token}, to={recipient}, amount={amount}")

        # Build transaction to call opp_contract.<target_fn_name>(*call_args)
        fn = getattr(opp_contract.functions, target_fn_name, None)
        if fn is None:
            print(f"Error: target function {target_fn_name} not found on opposite contract ABI.")
            continue

        # Build transaction dictionary
        try:
            from_address = opp_address if opp_address else (opp_info.get("from_address") or None)
            if from_address is None:
                # If no from address provided, we can't build a proper tx for broadcasting.
                # We'll still create an unsigned tx dict with a sample nonce for display.
                nonce = 0
            else:
                nonce = w3_opp.eth.get_transaction_count(from_address)
        except Exception as e:
            print("Warning getting nonce for opposite account:", e)
            nonce = 0

        # Try to estimate gas; fall back to a sensible default on failure
        try:
            gas_estimate = fn(*call_args).estimate_gas({'from': from_address}) if from_address else fn(*call_args).estimate_gas()
        except Exception:
            gas_estimate = 300000

        tx = {
            'from': from_address if from_address else "0x0",
            'to': opp_addr,
            'nonce': nonce,
            'gas': gas_estimate + 20000,
            'gasPrice': w3_opp.eth.gas_price,
            # value is zero for these bridge function calls
        }

        # Attach the function call data
        try:
            built = fn(*call_args).build_transaction(tx)
        except Exception as e:
            print("Failed to build transaction for function call:", e)
            continue

        # If we have a private key, sign and send. Otherwise just print the tx we would send.
        if opp_private_key:
            try:
                signed = w3_opp.eth.account.sign_transaction(built, private_key=opp_private_key)
                tx_hash = w3_opp.eth.send_raw_transaction(signed.rawTransaction)
                print(f"[{opp_chain}] Sent {target_fn_name} tx: {tx_hash.hex()}")
            except Exception as e:
                print(f"Error signing/sending tx on {opp_chain}:", e)
        else:
            # No private key: print the transaction details so user can broadcast manually if desired.
            print(f"--- DRY RUN (no private key for {opp_chain}) ---")
            print(f"Would call {opp_chain}.{target_fn_name} with args: {call_args}")
            print("Built transaction payload (unsigned):")
            # avoid printing huge binary data; show the key fields
            printable = {
                'to': built.get('to'),
                'from': built.get('from'),
                'nonce': built.get('nonce'),
                'gas': built.get('gas'),
                'gasPrice': built.get('gasPrice'),
                'data_hex_prefix': built.get('data')[:20] if isinstance(built.get('data'), str) else None
            }
            print(json.dumps(printable, indent=2, default=str))
            print("--- end dry run ---")

    print("Done processing events for chain:", chain)
    return 1
