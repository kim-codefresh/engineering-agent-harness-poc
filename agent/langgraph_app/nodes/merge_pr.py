def merge_pr(state):
    pr = state["pr"]
    print(f"[merge_pr] PR #{pr['number']} merged [MOCK] — CI/CD takes over from here")
    return {"pr": {**pr, "status": "merged"}}
