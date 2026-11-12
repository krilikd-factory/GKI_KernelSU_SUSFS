#!/usr/bin/env python3
"""Make the android16-6.12 BBRv3 backport modversions-neutral.

The backport keeps struct layouts intact (it says so, and the asserts added
below prove it at compile time), but two of its edits are hand-written unions
rather than ANDROID_KABI_* macros, so genksyms still sees them and every KMI
symbol that reaches struct tcp_sock would get a new CRC - and the stock vendor
modules would then refuse to load.

This script wraps exactly those two edits in __GENKSYMS__ guards - the same
mechanism ANDROID_KABI_USE / ANDROID_KABI_IGNORE use - so genksyms keeps
hashing the stock declarations, and appends static_asserts to tcp_bbr3.c that
fail the build if the layout did move:

  * include/linux/tcp.h   : the byte inserted after keepalive_probes must sit
                            in the existing padding hole before tcp_tx_delay.
  * include/net/tcp.h     : tcp_skb_cb.tx's {u64,u64} -> {u32 x4} union must
                            keep the same 16 bytes.

Run from the kernel/common directory after the BBRv3 patch applied.
"""
import re
import sys

TCP_SOCK_UNION = """\tu8	keepalive_probes; /* num of allowed keep alive probes	*/
\tunion {
\t\tu8	__kabi_ignored_0;
\t\tstruct {
\t\t\tu8	fast_ack_mode:2, /* which fast ack mode ? */
\t\t\t\ttlp_orig_data_app_limited:1, /* app-limited before TLP rtx? */
\t\t\t\tpad_unused:5;
\t\t};
\t};
"""

ASSERTS = r"""

/* CI (bbrv3_kabi_hide.py): the BBRv3 fields are hidden from genksyms, so
 * prove here that they really did not move anything. */
/* The stock layout had padding right after keepalive_probes ... */
static_assert((offsetof(struct tcp_sock, keepalive_probes) + 1) %
	      __alignof__(u32) != 0,
	      "BBRv3: no padding after keepalive_probes, tcp_sock layout moved");
/* ... and tcp_tx_delay still sits where stock put it. */
static_assert(offsetof(struct tcp_sock, tcp_tx_delay) ==
	      ALIGN(offsetof(struct tcp_sock, keepalive_probes) + 1,
		    __alignof__(u32)),
	      "BBRv3: tcp_sock.tcp_tx_delay moved");
static_assert(sizeof(((struct tcp_skb_cb *)0)->tx) == 24,
	      "BBRv3: tcp_skb_cb.tx changed size");
"""


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    # 1) struct tcp_sock: hide the inserted union from genksyms.
    p = "include/linux/tcp.h"
    s = open(p).read()
    if "/* BBRv3: in padding */" not in s:
        # Normalise whitespace for the search, but replace in the real text.
        pat = re.compile(
            r"(\tu8\s+keepalive_probes;[^\n]*\n)"
            r"(\tunion \{\n\t\tu8\s+__kabi_ignored_0;\n\t\tstruct \{\n"
            r"(?:.*\n)*?\t\t\};\n\t\};\n)")
        m = pat.search(s)
        if not m:
            die(f"{p}: BBRv3 tcp_sock union not found (patch changed?)")
        s = (s[:m.start(2)] + "#ifndef __GENKSYMS__ /* BBRv3: in padding */\n"
             + m.group(2) + "#endif\n" + s[m.end(2):])
        open(p, "w").write(s)
        print(f"{p}: tcp_sock BBRv3 union hidden from genksyms")

    # 2) tcp_skb_cb.tx: keep genksyms hashing the stock two u64 fields.
    p = "include/net/tcp.h"
    s = open(p).read()
    if "/* BBRv3: stock view */" not in s:
        pat = re.compile(
            r"(\t\t\tunion \{\n\t\t\t\tstruct \{\n"
            r"\t\t\t\t\tu64 __kabi_renamedfirst_tx_mstamp;\n"
            r"(?:.*\n)*?\t\t\t\t\};\n\t\t\t\};\n)")
        m = pat.search(s)
        if not m:
            die(f"{p}: BBRv3 tcp_skb_cb.tx union not found (patch changed?)")
        stock = ("\t\t\t/* start of send pipeline phase */\n"
                 "\t\t\tu64 first_tx_mstamp;\n"
                 "\t\t\t/* when we reached the \"delivered\" count */\n"
                 "\t\t\tu64 delivered_mstamp;\n")
        s = (s[:m.start(1)] + "#ifdef __GENKSYMS__ /* BBRv3: stock view */\n"
             + stock + "#else\n" + m.group(1) + "#endif\n" + s[m.end(1):])
        open(p, "w").write(s)
        print(f"{p}: tcp_skb_cb.tx BBRv3 union hidden from genksyms")

    # 3) compile-time layout proof.
    p = "net/ipv4/tcp_bbr3.c"
    s = open(p).read()
    if "bbrv3_kabi_hide.py" not in s:
        open(p, "a").write(ASSERTS)
        print(f"{p}: layout static_asserts appended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
