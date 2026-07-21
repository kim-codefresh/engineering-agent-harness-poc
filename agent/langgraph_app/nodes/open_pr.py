def open_pr(state):
    fix = state["fix"]
    evidence = state["evidence"]
    validation = state["validation"]

    pr = {
        "number": 42,
        "url": "https://github.com/octopus/fire-and-motion/pull/42 [MOCK]",
        "title": fix["commit_message"],
        "diff_summary": fix["diff_summary"],
        "tests_passed": validation["tests_run"],
        "scan_clean": validation["scan_clean"],
        "status": "draft",
    }

    print(f"[open_pr] Draft PR opened: {pr['url']}")
    print(f"[open_pr] Title: {pr['title']}")
    return {"pr": pr}
