import json, os
def env(n,d=""): return os.environ.get(n,d).strip() or d
def parse_managers(raw):
    try: return json.loads(raw) if raw.strip() else []
    except: return []
root = env("ROOT_FLAVOR","All")
if root.startswith("All"): root = "All"
feats=[]
if env("USE_SUSFS","true")=="true": feats.append("SUSFS")
if env("USE_NOMOUNT","true")=="true": feats.append("NoMount")
if env("USE_BBG","true")=="true": feats.append("Baseband Guard")
if env("USE_DS","true")=="true": feats.append("DroidSpaces-OSS")
if env("USE_NET","true")=="true": feats.append("Networking")
if env("USE_NTSYNC","true")=="true": feats.append("NTSync")
if env("USE_PTRACE","true")=="true": feats.append("Ptrace Leak Fix")
if env("USE_UNICODE","true")=="true": feats.append("Unicode Fix")
if env("USE_BPF","true")=="true": feats.append("BTF / eBPF / FUSE-BPF")
if env("USE_PERF","false") in ("true","bore"): feats.append("Performance Tuning")
if env("USE_BORE","false")=="true": feats.append("BORE scheduler (experimental; android16-6.12 only)")
if env("USE_ADIOS","false")=="true": feats.append("ADIOS 3.3.0 I/O scheduler (built-in; runtime-selectable, not default)")
_fs=set(t for t in env("FEATURE_SET","").split("+") if t)
if "VPN" in _fs: feats.append("VPN Hide (built-in)")
if "USB" in _fs: feats.append("USB Arsenal")
if "RK" in _fs: feats.append("Re-Kernel")
if "AWL" in _fs: feats.append("ASB wakeup blocker (empty by default)")
if "SDP900" in _fs: feats.append("OnePlus SDP 900 mA")
if env("PAGE_SIZE","4k")=="16k": feats.append("16 KB page size (ARM64)")

def _read_prop(path, key):
    try:
        for ln in open(path, encoding="utf-8", errors="replace"):
            if ln.startswith(key + "="):
                return ln.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""

def _find(root, name):
    if not root or not os.path.isdir(root):
        return []
    out = []
    for d, _, files in os.walk(root):
        if name in files:
            out.append(os.path.join(d, name))
    return sorted(out)

_pins = {}
for _pf in _find(env("PINS_DIR", ""), "known-good-candidate.json"):
    try:
        for _k, _v in json.load(open(_pf, encoding="utf-8")).items():
            if isinstance(_v, dict):
                _pins[_k] = _v
    except Exception:
        pass

_mod_versions = {}
for _mp in _find(env("MODULES_DIR", ""), "module.prop"):
    _id = _read_prop(_mp, "id")
    _ver = _read_prop(_mp, "version")
    if _id and _ver:
        _mod_versions[_id] = _ver

def _root_label(v):
    ver = (v.get("root_version") or "").removesuffix("-dirty")
    code = v.get("root_version_code") or ""
    if ver and code:
        return f"{ver} (version code {code})"
    return ver or (f"version code {code}" if code else "")

def _root_version(commit):
    for _v in _pins.values():
        if commit and _v.get("root_commit") == commit:
            return _root_label(_v)
    return ""

brand = env("BRAND_NAME", "")
lines=[]
lines.append(f"# {brand} kernel for GKI2 devices" if brand else "# GKI2 kernel build")
lines.append("")
if _pins:
    lines.append("## Builds in this run")
    lines.append("")
    lines.append("| Build | Kernel version string | Root | Features |")
    lines.append("|---|---|---|---|")
    for _k in sorted(_pins):
        _v = _pins[_k]
        lines.append(f"| `{_v.get('file', _k)}` | `{_v.get('kernel_version_string') or 'see BuildInfo'}` | {_root_label(_v) or '-'} | {_v.get('feature_set') or '-'} |")
    lines.append("")
lines.append("## Root")
lines.append("")
ksun_branch=env("KSUN_BRANCH","dev-susfs")
ksun_commit=env("KSUN_COMMIT","")
ksu_commit=env("KSU_COMMIT","")
bakasu_commit=env("BAKASU_COMMIT","")
sukisu_ultra_commit=env("SUKISU_ULTRA_COMMIT","")
finasu_commit=env("FINASU_COMMIT","")
kowsu_commit=env("KOWSU_COMMIT","")
kernelsu_commit=env("KERNELSU_COMMIT","")
nm=env("NOMOUNT_COMMIT","")
kp=env("KERNEL_PATCHES_COMMIT","")
ak=env("ANYKERNEL3_COMMIT","")
ds=env("DROIDSPACES_COMMIT","")
_rmatrix={}
try: _rmatrix={m.get("root_flavor",""):m.get("root_commit","") for m in json.loads(env("ROOT_MATRIX","[]")) if isinstance(m,dict)}
except: pass
if env("USE_SUSFS","true")=="true":
    _next_url="https://github.com/pershoot/KernelSU-Next/commit/"
else:
    _next_url="https://github.com/KernelSU-Next/KernelSU-Next/commit/"
_root_urls={"next":_next_url,"kernelsu":"https://github.com/tiann/KernelSU/commit/","bakasu":"https://github.com/Baka-SU/BakaSU/commit/","sukisu-ultra":"https://github.com/SukiSU-Ultra/SukiSU-Ultra/commit/","finasu":"https://github.com/LyraVoid/FinaSU/commit/","kowsu":"https://github.com/KOWX712/KernelSU/commit/"}
_root_commits={"next":_rmatrix.get("next","") or kernelsu_commit,"kernelsu":_rmatrix.get("kernelsu","") or ksu_commit,"bakasu":_rmatrix.get("bakasu","") or bakasu_commit,"sukisu-ultra":_rmatrix.get("sukisu-ultra","") or sukisu_ultra_commit,"finasu":_rmatrix.get("finasu","") or finasu_commit,"kowsu":_rmatrix.get("kowsu","") or kowsu_commit}
_flavors0=[m.get("flavor","") for m in parse_managers(env("MANAGER_LIST","")) if m.get("flavor","")]
if not _flavors0:
    _flavors0={"KernelSU-Next":["next"],"KernelSU":["kernelsu"],"BakaSU":["bakasu"],"SukiSU-Ultra":["sukisu-ultra"],"FinaSU":["finasu"],"KowSU":["kowsu"],"No Root":[],"All":["next","kernelsu","bakasu"]}.get(root,["next","kernelsu","bakasu"])
_labels0={"next":"KernelSU-Next","kernelsu":"KernelSU","bakasu":"BakaSU","sukisu-ultra":"SukiSU-Ultra","finasu":"FinaSU","kowsu":"KowSU"}
_rc=[]
for _fl0 in _flavors0:
    _c=_root_commits.get(_fl0,"")
    _rv=_root_version(_c)
    _rvs=f" {_rv}" if _rv else ""
    if _c and len(_c)==40: _rc.append(f"{_labels0.get(_fl0,_fl0)}{_rvs} [`{_c[:7]}`]({_root_urls[_fl0]}{_c})")
    elif _c: _rc.append(f"{_labels0.get(_fl0,_fl0)} `{_c}`")
for _r in _rc: lines.append(f"- {_r}")
lines.append("")
lines.append("## Features")
lines.append("")
_kp_url="https://github.com/WildKernels/kernel_patches/commit/"
_feat_commit={"NoMount":("https://github.com/maxsteeel/nomount/commit/",nm),"Baseband Guard":(_kp_url,kp),"DroidSpaces-OSS":("https://github.com/ravindu644/Droidspaces-OSS/commit/",ds),
    "VPN Hide (built-in)":("https://github.com/okhsunrog/vpnhide/commit/",env("VPNHIDE_COMMIT","")),
    "USB Arsenal":("https://github.com/ravindu644/USB-Arsenal/commit/",env("USB_ARSENAL_COMMIT","")),
    "Re-Kernel":("https://github.com/Sakion-Team/Re-Kernel/commit/",next((v.get("rekernel_commit","") for v in _pins.values() if v.get("rekernel_commit")),""))}
_feat_version={"NoMount":_mod_versions.get("nomount",""),"VPN Hide (built-in)":_mod_versions.get("vpnhide_builtin","")}
for f in feats:
    _u,_c=_feat_commit.get(f,("",""))
    _fv=_feat_version.get(f,"")
    if _fv: f=f"{f} {_fv}"
    if _c and len(_c)==40: lines.append(f"- {f} [`{_c[:7]}`]({_u}{_c})")
    elif _c: lines.append(f"- {f} `{_c}`")
    else: lines.append(f"- {f}")
if not feats: lines.append("- (none)")
kp=env("KERNEL_PATCHES_COMMIT","")
ak=env("ANYKERNEL3_COMMIT","")
ds=env("DROIDSPACES_COMMIT","")
lines.append("")
lines.append("## SUSFS commits")
lines.append("")
all_variants=["android12-5.10","android13-5.10","android13-5.15","android14-5.15","android14-6.1","android15-6.6","android16-6.12"]
def kbv_to_variant(kbv):
    if ".x-android" in kbv:
        kv, av = kbv.split(".x-android", 1)
        v = f"android{av}-{kv}"
        return v if v in all_variants else ""
    return ""
kbv=env("KERNEL_BUILD_VERSION","All")
_sel=kbv_to_variant(kbv)
variants=[_sel] if _sel else list(all_variants)
sus_map={"android12-5.10":env("SUSFS_ANDROID12_5_10",""),"android13-5.10":env("SUSFS_ANDROID13_5_10",""),"android13-5.15":env("SUSFS_ANDROID13_5_15",""),"android14-5.15":env("SUSFS_ANDROID14_5_15",""),"android14-6.1":env("SUSFS_ANDROID14_6_1",""),"android15-6.6":env("SUSFS_ANDROID15_6_6",""),"android16-6.12":env("SUSFS_ANDROID16_6_12","")}
sus_map_bakasu={"android12-5.10":env("SUSFS_ANDROID12_5_10_BAKASU",""),"android13-5.10":env("SUSFS_ANDROID13_5_10_BAKASU",""),"android13-5.15":env("SUSFS_ANDROID13_5_15_BAKASU",""),"android14-5.15":env("SUSFS_ANDROID14_5_15_BAKASU",""),"android14-6.1":env("SUSFS_ANDROID14_6_1_BAKASU",""),"android15-6.6":env("SUSFS_ANDROID15_6_6_BAKASU",""),"android16-6.12":env("SUSFS_ANDROID16_6_12_BAKASU","")}
sus_map_sukisu_ultra={"android12-5.10":env("SUSFS_ANDROID12_5_10_SUKISU_ULTRA",""),"android13-5.10":env("SUSFS_ANDROID13_5_10_SUKISU_ULTRA",""),"android13-5.15":env("SUSFS_ANDROID13_5_15_SUKISU_ULTRA",""),"android14-5.15":env("SUSFS_ANDROID14_5_15_SUKISU_ULTRA",""),"android14-6.1":env("SUSFS_ANDROID14_6_1_SUKISU_ULTRA",""),"android15-6.6":env("SUSFS_ANDROID15_6_6_SUKISU_ULTRA",""),"android16-6.12":env("SUSFS_ANDROID16_6_12_SUKISU_ULTRA","")}
_flavors=[m.get("flavor","") for m in parse_managers(env("MANAGER_LIST","")) if m.get("flavor","")]
if not _flavors:
    _flavors={"KernelSU-Next":["next"],"KernelSU":["kernelsu"],"BakaSU":["bakasu"],"SukiSU-Ultra":["sukisu-ultra"],"KowSU":["kowsu"],"No Root":[],"All":["next","kernelsu","bakasu"]}.get(root,["next","kernelsu","bakasu"])
_labels={"next":"KernelSU-Next","kernelsu":"KernelSU","bakasu":"BakaSU","sukisu-ultra":"SukiSU-Ultra","kowsu":"KowSU"}
_susfs_url="https://gitlab.com/simonpunk/susfs4ksu"
lines.append("| SUSFS branch |" + "".join(f" {_labels.get(_fl,_fl)} |" for _fl in _flavors))
lines.append("|---" * (len(_flavors) + 1) + "|")
for v in variants:
    _row=f"| gki-{v} |"
    for _fl in _flavors:
        # KowSU never ships SUSFS: render n/a rather than shared pins.
        _sm={} if _fl=="kowsu" else (sus_map_sukisu_ultra if _fl=="sukisu-ultra" else (sus_map_bakasu if _fl=="bakasu" else sus_map))
        sha=_sm.get(v,"")
        sha_s=f"[`{sha[:7]}`]({_susfs_url}/-/commit/{sha})" if sha else "_n/a_"
        _row+=f" {sha_s} |"
    lines.append(_row)
lines.append("")
lines.append("")
# --- build availability -----------------------------------------------------
# Only an "All" run spans the matrix; a single-family run says nothing about
# the other families, so the section is left out there.
_disabled={}
if not _sel:
    try:
        import glob as _glob
        _root=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for _p in sorted(_glob.glob(os.path.join(_root,".github","config","*.json")) or _glob.glob(".github/config/*.json")):
            try: _cfg=json.load(open(_p))
            except Exception: continue
            _rows=sorted(e.get("date","") for e in _cfg.get("include",[]) if e.get("disabled") is True)
            if _rows: _disabled[_cfg.get("version","")]=_rows
    except Exception:
        _disabled={}
if _disabled:
    lines.append("## Build availability")
    lines.append("")
    _total=sum(len(v) for v in _disabled.values())
    lines.append(f"**{_total} matrix rows are switched off** because the same kernel version is built from another month:")
    lines.append("")
    for _fam in sorted(_disabled):
        lines.append(f"- `{_fam}`: {', '.join(_disabled[_fam])}")
    lines.append("")
    _repo=env("GITHUB_REPOSITORY","")
    lines.append("They are marked `\"disabled\": true` in `.github/config/*.json`" + (f"; ask in https://github.com/{_repo}/issues to re-enable one." if _repo else "."))
    lines.append("")
output="\n".join(lines)+"\n"
print(output,end="")
s=os.environ.get("GITHUB_STEP_SUMMARY")
if s: open(s,"a").write(output)
