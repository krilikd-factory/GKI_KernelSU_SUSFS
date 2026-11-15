#!/usr/bin/env python3
"""Port the three BORE 7.0.0 hunks that do not fit android16-6.12 (6.12.69 GKI) by hand.

BORE 7.0.0 was cut against upstream 6.12.37. The Android common kernel moved on, so after
`patch -F3` three places need care:

  1. sysctl block (hunk rejected): GKI keeps its own 700000ULL slice and exports
     sysctl_sched_base_slice. The BORE variables are added under CONFIG_SCHED_BORE and the stock
     ones stay in the #else, with the export outside the #ifdef so the KMI symbol is unchanged.
  2. update_curr_bore() call (fuzz-spliced): the hunk lands inside the fair_server comment of
     update_curr(). It is moved to its real place after `if (entity_is_task(curr)) {` and calls
     task_of(curr), because GKI no longer has a local `p` there.
  3. pick_eevdf slice protection (hunk rejected): GKI reached the protect-slice code through
     __pick_eevdf(cfs_rq, protect). BORE's condition is applied to that exact `return curr`.

Every edit is anchor-checked and the script fails when an anchor is missing: a tree that drifted
must stop the build instead of shipping a half-ported scheduler. Re-running is a no-op.

Usage: bore_gki612_fixups.py <kernel/sched/fair.c>
"""
import re
import sys

MARK = "bore-gki612"


def die(msg):
    print("bore-gki612: " + msg, file=sys.stderr)
    sys.exit(1)


def once(s, old, new, what):
    if s.count(old) != 1:
        die("anchor for %s found %d times (expected 1)" % (what, s.count(old)))
    return s.replace(old, new)


def fix_sysctl(s):
    if "static const unsigned int nsecs_per_tick" in s:
        return s, "sysctl: already present"
    s = once(
        s,
        "unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_LOG;\n",
        "#ifdef CONFIG_SCHED_BORE\n"
        "unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_NONE;\n"
        "#else /* !CONFIG_SCHED_BORE */\n"
        "unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_LOG;\n"
        "#endif /* CONFIG_SCHED_BORE */\n",
        "tunable_scaling",
    )
    s = once(
        s,
        "unsigned int sysctl_sched_base_slice\t\t\t= 700000ULL;\n"
        "EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);\n"
        "static unsigned int normalized_sysctl_sched_base_slice\t= 700000ULL;\n",
        "#ifdef CONFIG_SCHED_BORE /* " + MARK + ": base slice */\n"
        "static const unsigned int nsecs_per_tick       = 1000000000ULL / HZ;\n"
        "unsigned int sysctl_sched_min_base_slice       = CONFIG_MIN_BASE_SLICE_NS;\n"
        "__read_mostly uint sysctl_sched_base_slice     = nsecs_per_tick;\n"
        "#else /* !CONFIG_SCHED_BORE */\n"
        "unsigned int sysctl_sched_base_slice\t\t\t= 700000ULL;\n"
        "#endif /* CONFIG_SCHED_BORE */\n"
        "EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);\n"
        "static unsigned int normalized_sysctl_sched_base_slice __maybe_unused\t= 700000ULL;\n",
        "base_slice",
    )
    return s, "sysctl: ported"


def fix_update_curr(s):
    if "update_curr_bore" not in s:
        die("update_curr_bore is not in fair.c; the BORE patch did not apply")
    m = re.search(r"#ifdef CONFIG_SCHED_BORE\n\t\tupdate_curr_bore\(.*\);\n#endif /\* CONFIG_SCHED_BORE \*/\n", s)
    if not m:
        die("update_curr_bore call site not found")
    before = s[:m.start()].rstrip("\n").split("\n")[-1]
    if not re.match(r"\s*(\*|/\*)", before):
        return s, "update_curr: already anchored"
    s = s[:m.start()] + s[m.end():]
    anchor = "\tif (entity_is_task(curr)) {\n"
    pos = s.rfind(anchor, 0, m.start())
    if pos < 0 or s[pos:m.start()].count("\n") > 4:
        die("'if (entity_is_task(curr)) {' anchor not found in update_curr()")
    pos += len(anchor)
    call = ("#ifdef CONFIG_SCHED_BORE\n\t\tupdate_curr_bore(task_of(curr), delta_exec);\n"
            "#endif /* CONFIG_SCHED_BORE */\n")
    return s[:pos] + call + s[pos:], "update_curr: relocated"


def fix_protect(s):
    if MARK + ": protect" in s:
        return s, "protect: already present"
    old = "\tif (curr && protect && protect_slice(curr))\n\t\treturn curr;\n"
    new = ("\tif (curr && protect && protect_slice(curr)) {\n"
           "#ifdef CONFIG_SCHED_BORE /* " + MARK + ": protect */\n"
           "\t\tif (static_branch_likely(&sched_bore_key)) {\n"
           "\t\t\tif ((static_branch_likely(&sched_burst_protect_slice_cond_key) ||\n"
           "\t\t\t     static_branch_unlikely(&sched_burst_protect_slice_prefer_key)) &&\n"
           "\t\t\t    (!entity_is_task(curr) || !task_of(curr)->bore ||\n"
           "\t\t\t     !task_of(curr)->bore->futex_waiting))\n"
           "\t\t\t\treturn curr;\n"
           "\t\t} else\n"
           "#endif /* CONFIG_SCHED_BORE */\n"
           "\t\t\treturn curr;\n"
           "\t}\n")
    return once(s, old, new, "pick_eevdf protect_slice"), "protect: ported"


def main():
    if len(sys.argv) != 2:
        die("usage: bore_gki612_fixups.py <fair.c>")
    path = sys.argv[1]
    s = open(path, encoding="utf-8").read()
    for fn in (fix_sysctl, fix_update_curr, fix_protect):
        s, note = fn(s)
        print("bore-gki612: " + note)
    open(path, "w", encoding="utf-8").write(s)


if __name__ == "__main__":
    main()
