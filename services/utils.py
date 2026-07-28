def success(message):
    return {
        "success": True,
        "message": message
    }


def error(message):
    return {
        "success": False,
        "message": message
    }