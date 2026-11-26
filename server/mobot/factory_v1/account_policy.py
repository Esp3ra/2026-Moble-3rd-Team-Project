"""AccountCreate and Account display-name limits in the shared OpenAPI."""
ACCOUNT_DISPLAY_NAME_MAX = 128
ACCOUNT_DISPLAY_NAME_RESPONSE_MAX = 100


def account_view(data):
    """Keep the stored name intact and limit the account DTO, including old replays."""
    return {**data, "display_name": data["display_name"][:ACCOUNT_DISPLAY_NAME_RESPONSE_MAX]}
