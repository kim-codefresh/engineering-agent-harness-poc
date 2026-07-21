def scope_authorization(state):
    """
    Gate 2 — repository owner scope authorization.

    Runner injects scope_authorized into state before this node runs.
    """
    authorized = state.get("scope_authorized", False)
    print(f"[scope_authorization] Scope {'authorized ✅' if authorized else 'rejected ❌'}")
    return {"scope_authorized": authorized}
