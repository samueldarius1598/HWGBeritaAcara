# Google Sheet Credentials

Folder ini dipakai sebagai pusat konfigurasi credential yang disimpan di Google Sheet
(gid 1746209771), selain Supabase.

## Konfigurasi
Set environment variable atau isi `secrets.toml`:
- `CREDENTIALS_GAS_URL` = URL Web App Apps Script (ending `/exec`)
- `CREDENTIALS_GAS_SECRET` = nilai `key` untuk autentikasi
- `CREDENTIALS_GID` = gid sheet (default 1746209771)
- `CREDENTIALS_SHEET_NAME` = nama sheet (opsional, contoh: `secretCredentials`)
- `CREDENTIALS_GAS_TIMEOUT` = timeout request (opsional)

## Mapping cell
Edit mapping di `core/credentials/maps.py` untuk menentukan cell mana yang dipakai.

## Contoh pakai
```python
from core.credentials import (
    DEFAULT_CREDENTIALS_GID,
    DEFAULT_CREDENTIALS_SHEET,
    ESB_CREDENTIAL_CELLS,
    GoogleSheetCredentialsConfig,
    GoogleSheetCredentialsStore,
    build_esb_credentials,
)

config = GoogleSheetCredentialsConfig.from_settings(
    default_gid=DEFAULT_CREDENTIALS_GID,
    default_sheet_name=DEFAULT_CREDENTIALS_SHEET,
)
store = GoogleSheetCredentialsStore(config)
raw_values = store.fetch_fields(ESB_CREDENTIAL_CELLS)
esb_creds = build_esb_credentials(raw_values)
```
