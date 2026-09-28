# API keys and builder fees

## API keys

For existing registered keys, the [CLI profile workflow](commands.md#local-profiles)
imports the key into a local encrypted keystore and checks registration before
trading. It does not register a key. Use the SDK lifecycle below for registration,
revocation and builder actions outside the CLI.

`SODEX_API_KEY_NAME` is the registered name sent in `X-API-Key`, not an address
or a secret. With API-key signing, `SODEX_PRIVATE_KEY` is that key's secret and
`SODEX_ACCOUNT_ADDRESS` is the master owner. Account queries use owner/account ID.

Names match `^[0-9a-zA-Z_-]{1,36}$`; `default` is reserved. An account can have at
most five keys. Check existing registration, account, public address and expiry
before creating another. Registration/revocation require the master wallet.
Use explicit network selection and keep master credentials separate from the
routine trading process.

List using the read-only owner configuration:

```bash
SODEX_API_KEY_ACTION=list "$SODEX_PYTHON" "$SODEX_SDK/examples/api_key.py"
```

The example defaults to `register` and its `save_to_secret_manager()` function
discards the generated secret. **Do not run its register branch unchanged.**
For one-step onboarding, have the user create/store a dedicated key through
their wallet or SoDEX interface locally, then configure the three variables
above. Verify it with `get_api_keys()` before placing an order.

For SDK-based registration, generate and store a separate EVM key using the
user's key-management system **before** calling `add_api_key()`. Obtain its
public address locally without printing the secret. This avoids losing a newly
registered key when a process exits or a registration response is ambiguous.

```python
import os
import time
from sodex.client import AddAPIKeyRequest, Client
from sodex.common.enums import APIKeyPermission

master = Client.from_env()  # Master signer, explicit network, no API-key name.
account_id = master.primary_account_id()
request = AddAPIKeyRequest(
    account_id=account_id,
    name=os.environ["SODEX_TARGET_API_KEY_NAME"],
    public_key=os.environ["SODEX_NEW_API_KEY_ADDRESS"],
    expires_at=int(time.time() * 1000) + 24 * 60 * 60 * 1000,
    permissions=int(APIKeyPermission.CANCEL | APIKeyPermission.WITHDRAW | APIKeyPermission.TRANSFER),
)
master.add_api_key(master.address, request)
print(master.get_api_keys(account_id=account_id, name=request.name))
```

Run only with authorized account, key address, name, expiry and permissions.
This example enables TRADE and disables the separate CANCEL capability plus
withdraw/transfer for 24 hours. TRADE itself includes order cancellation.
The permissioned-key endpoint requires at least one of TRADE/CANCEL to be
disabled; mask `12` is not a valid trading-only configuration. Mask `14`
enables trading (including cancellation) while disabling fund movements;
mask `13` instead permits cancellation only. Ordinary registration without a
permission mask enables all permissions and should not be described as trading-only.
**Set bits disable permissions:** TRADE=1, CANCEL=2, WITHDRAW=4, TRANSFER=8.
Omitting `permissions` enables all permissions; a zero mask is not read-only.
The SDK's `approve_agent()` convenience method generates and registers in one
call; use it only if the application handles secure persistence and recovery.

Verify name, public key, expiry and account in both `spot` and `perps` results.
Indexed reads may lag; on an ambiguous response inspect both engines before
retrying or declaring success. Each signer shares a nonce stream across its
subaccounts. The SDK coordinates threads within one process, not independent
processes; use separate registered keys or an external nonce coordinator.
The bundled CLI adds a durable nonce counter and process lock for commands
sharing one state directory, but cannot coordinate unrelated SDK applications.

For an authorized revoke, set `SODEX_TARGET_API_KEY_NAME` and run with the
master wallet:

```bash
SODEX_API_KEY_ACTION=revoke "$SODEX_PYTHON" "$SODEX_SDK/examples/api_key.py"
```

List again to verify revocation on both engines.

## Builder approval and attributed orders

Start from `examples/approve_builder_fee.py`. Obtain the authorized builder ID
and maximum fee rate, then approve with the master wallet. A builder is an
activated account holding at least 100 vUSDC in each engine; there is no separate
builder registration. These balance requirements also apply when clearing an
approval. Do not add a builder to ordinary orders unless the user asks.

```bash
# SODEX_BUILDER_ID and SODEX_BUILDER_FEE_RATE must be explicitly configured.
"$SODEX_PYTHON" "$SODEX_SDK/examples/approve_builder_fee.py"
```

Rates are **tenths of a basis point**: `10` = 1 bp, `20` = 2 bp (0.02%). The
approval range is 0–2000; 0 clears approval. Order caps are 2000 for Spot (2%)
and 200 for Perps (0.2%), and the rate must fit the user's approval. At most
ten builder approvals are allowed. Approval applies across both engines via
Gateway but is not atomic; inspect each engine after an ambiguous/partial result.

The pinned SDK has no builder-list helper. Query the documented public endpoint
with the selected Gateway root and master address:

```python
import requests

response = requests.get(
    f"{master.base_url}/api/v1/user/{master.account_address}/builders", timeout=10,
)
response.raise_for_status()
result = response.json()
if result["code"] != 0:
    raise RuntimeError("Builder query failed")
print(result["data"])  # Check builderID/feeRate in spot and perps.
```

Builder fees apply to both Perps sides and to Spot fees collected in the quote
asset; they do not apply to the Spot buying side.

For order submission, import `BuilderParams` from `sodex.client` and pass it to
`spot_order()` / `perps_order()`. Omitting it omits the signed builder payload.
Perps batches permit a batch default and per-order `RawOrder(builder=...)`
overrides; Spot uses batch attribution. Follow the pinned SDK signatures.

Sources: [API keys and nonces](https://sodex.com/documentation/for-developers/developers/trading/api-keys-and-nonces),
[builder codes](https://sodex.com/documentation/for-developers/developers/trading/builder-codes-in-trading),
[SDK guide](https://sodex.com/documentation/for-developers/sdks/python-sdk-guide).
