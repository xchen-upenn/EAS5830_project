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



# --- SCAN BLOCKS & TRIGGER BRIDGE ---
def scan_blocks(chain, contract_info="contract_info.json"):
    """
    Scan last SCAN_BLOCKS blocks on the source/destination chain,
    detect Deposit/Unwrap events, and call wrap/withdraw on the opposite chain.
    """
    if chain not in ['source', 'destination']:
        print(f"Invalid chain: {chain}")
        return 0

    # Number of blocks to scan (hardcoded locally)
    num_blocks_to_scan = 5

    
    # Connect to this chain and load contract
    w3 = connect_to(chain)
    this_info = get_contract_info(chain, contract_info)
    this_contract = w3.eth.contract(
        address=Web3.to_checksum_address(this_info["address"]),
        abi=this_info["abi"]
    )

    # Connect to opposite chain
    opp_chain = 'destination' if chain == 'source' else 'source'
    w3_opp = connect_to(opp_chain)
    opp_info = get_contract_info(opp_chain, contract_info)
    opp_contract = w3_opp.eth.contract(
        address=Web3.to_checksum_address(opp_info["address"]),
        abi=opp_info["abi"]
    )

    # Opposite account (for sending transactions)
    opp_key = opp_info.get("warden_private_key")
    opp_account = w3_opp.eth.account.from_key(opp_key) if opp_key else None

    # Determine which events and functions to handle
    if chain == 'source':
        event_obj = this_contract.events.Deposit
        target_fn = 'wrap'
    else:
        event_obj = this_contract.events.Unwrap
        target_fn = 'withdraw'

    # Scan last num_blocks_to_scan blocks
    latest = w3.eth.block_number
    start_block = max(0, latest - num_blocks_to_scan)
    try:
        events = event_obj.create_filter(fromBlock=start_block, toBlock=latest).get_all_entries()
    except:
        events = []

    # Process each event
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

        # Call the target function on opposite chain
        if opp_account:
            nonce = w3_opp.eth.get_transaction_count(opp_account.address, "pending")
            tx = getattr(opp_contract.functions, target_fn)(*call_args).build_transaction({
                'from': opp_account.address,
                'nonce': nonce,
                'gas': 300000,
                'gasPrice': w3_opp.eth.gas_price
            })
            signed = w3_opp.eth.account.sign_transaction(tx, private_key=opp_key)
            tx_hash = w3_opp.eth.send_raw_transaction(signed.rawTransaction)
            print(f"[{opp_chain}] Called {target_fn} -> tx: {tx_hash.hex()}")
