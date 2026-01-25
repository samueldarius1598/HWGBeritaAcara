DEFAULT_CREDENTIALS_GID = "1746209771"
DEFAULT_CREDENTIALS_SHEET = "secretCredentials"

# Atur cell sesuai kebutuhan. Ganti alamat cell ini jika layout sheet berubah.
ESB_CREDENTIAL_CELLS = {
    "username_part1": "E2",
    "username_part2": "E3",
    "password": "E4",
    "company_code": "E7",
    "company_name": "E8",
    "access_token": "E9",
    "refresh_token": "E10",
    "token_timestamp": "E11",
}

ESB_CREDENTIAL_RANGE = "E2:E11"
ESB_TOKEN_WRITE_RANGE = "E7:E11"


def build_esb_credentials(values):
    username = f"{values.get('username_part1', '')}{values.get('username_part2', '')}".strip()
    return {
        "username": username,
        "password": values.get("password", ""),
        "company_code": values.get("company_code", ""),
        "company_name": values.get("company_name", ""),
        "access_token": values.get("access_token", ""),
        "refresh_token": values.get("refresh_token", ""),
        "token_timestamp": values.get("token_timestamp", ""),
    }
