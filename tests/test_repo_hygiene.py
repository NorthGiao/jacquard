"""Repo hygiene: the writer patch is reproducible, and no private information gets mixed into the publishable part."""
import re, shutil, subprocess
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parent.parent
PUBLISHABLE_EXCLUDE = {"private", ".pytest_cache", "__pycache__"}


def _git_publishable(tmp):
    """The files git would publish (tracked + untracked, minus .gitignore), or None without git. Works before `git init` too:
    a throwaway git dir in tmp is pointed at the repo as work tree, so nothing is written into the repo."""
    if not shutil.which("git"):
        return None
    cmd = ["git", "-c", "core.quotePath=false"]
    if not (REPO / ".git").exists():
        subprocess.run(["git", "init", "-q", "--bare", str(tmp / "g.git")], check=True, capture_output=True)
        cmd += ["--git-dir", str(tmp / "g.git"), "--work-tree", str(REPO), "-c", "core.bare=false"]
    r = subprocess.run(cmd + ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO, capture_output=True)
    if r.returncode:
        return None
    return [REPO / x for x in sorted(set(r.stdout.decode("utf-8").split("\0")) - {""})]


def files(tmp=None):
    got = _git_publishable(tmp) if tmp is not None else None
    if got is not None:
        yield from (p for p in got if p.is_file())
        return
    for p in REPO.rglob("*"):
        if p.is_file() and not (set(p.relative_to(REPO).parts) & PUBLISHABLE_EXCLUDE):
            yield p


@pytest.mark.skipif(not shutil.which("patch"), reason="no patch command")
def test_writer_equals_upstream_plus_patches(tmp_path):
    for f in ("gen.py", "lv.py"):
        shutil.copy(REPO / "writer" / "upstream" / f, tmp_path / f)
    for d in sorted((REPO / "writer" / "patches").glob("*.diff")):
        r = subprocess.run(["patch", "-p3", "-d", str(tmp_path), "-i", str(d)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    for f in ("gen.py", "lv.py"):
        assert (tmp_path / f).read_bytes() == (REPO / "writer" / "labview_mcp" / f).read_bytes(), \
            f"writer/labview_mcp/{f} changed but the patch wasn't updated: run scripts/regen_patch.sh"


PRIVATE_IP = re.compile(rb"\b(10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)\b")
HOME = re.compile(rb"/Users/[A-Za-z]|C:\\\\Users\\\\(?!<)|C:\\Users\\(?!<)|Windows11")
BAD_EXT = {".vi", ".llb", ".ctl", ".dmp", ".lvproj", ".pdf", ".dat", ".tdms"}


EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
DENYLIST = REPO / "private" / "publish_denylist.txt"   # local only (lab program names, account names); skipped when absent


def _denied():
    if not DENYLIST.exists():
        return []
    return [s.encode() for s in DENYLIST.read_text(encoding="utf-8").splitlines() if s.strip() and not s.startswith("#")]


def test_no_private_info_outside_private(tmp_path):
    bad, denied = [], _denied()
    for p in files(tmp_path):
        rel = p.relative_to(REPO)
        if p.suffix.lower() in BAD_EXT and rel.parts[:3] != ("writer", "labview_mcp", "vis"):
            bad.append(f"{rel}: extension {p.suffix} (VIs/binary files go in private/)")
        if p.stat().st_size > 1_000_000:
            bad.append(f"{rel}: >1 MB")
        if p.suffix in {".py", ".md", ".json", ".sh", ".txt", ".toml", ".ini"} and p.name != "test_repo_hygiene.py":
            b = p.read_bytes()
            if PRIVATE_IP.search(b):
                bad.append(f"{rel}: private IP")
            if HOME.search(b):
                bad.append(f"{rel}: home directory/personal account path (use the <LAB_USER> placeholder)")
            if EMAIL.search(b):
                bad.append(f"{rel}: e-mail address")
            hits = [d.decode() for d in denied if d in b]
            if hits:
                bad.append(f"{rel}: names from private/publish_denylist.txt: {hits[:5]}")
    assert not bad, "\n".join(bad)


LOCAL_STATE = {"jobs/verified.json", "catalog/probe_queue.json"}   # written by the tools, gitignored or created on first use: may be absent in a clone
SKILLS = sorted(d for d in (REPO / "skills").iterdir() if d.is_dir())


def test_installed_skill_matches_repo():
    """skills/<name>/SKILL.md is the master copy, .claude/skills/<name>/ in the parent directory is the installed copy: installed ones must be byte-identical, and no deleted old skills may remain.
    Skipped when someone clones the repo elsewhere without installing the skills (only checked when at least one skill of this repo is installed)."""
    ws = REPO.parent / ".claude" / "skills"
    assert SKILLS, "skills/ is empty"
    installed = [d for d in SKILLS if (ws / d.name / "SKILL.md").exists()]
    if not installed:
        pytest.skip("skills not installed in .claude/skills/ of the parent directory")
    for d in installed:
        assert (ws / d.name / "SKILL.md").read_bytes() == (d / "SKILL.md").read_bytes(), \
            f"{d.name}: a changed SKILL.md in skills/ must be copied to .claude/skills/ of the parent directory (or vice versa)"
    missing = [d.name for d in SKILLS if d not in installed]
    assert not missing, f"only some of the skills are installed, missing: {missing}"
    stale = [x.name for x in ws.iterdir() if x.name.startswith("labview-") and not (REPO / "skills" / x.name).exists()]
    assert not stale, f"the installed copies contain skills already deleted from the repo: {stale}"


def test_skill_frontmatter_and_paths():
    """name = directory name; the description states "Not for"; repo scripts referenced in the body all exist."""
    import re
    for d in SKILLS:
        t = (d / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", t)
        assert m and m.group(1) == d.name, f"{d.name}: the frontmatter name must equal the directory name"
        assert "Not for" in m.group(2), f"{d.name}: the description must state which cases it is not for (pointing to other skills)"
        for ref in set(re.findall(r"(?<![\w/])((?:readers|scripts|lab|jacq|jobs|docs|writer|tests|catalog)/[\w./-]+\.(?:py|sh|md|json))", t)):
            assert ref in LOCAL_STATE or (REPO / ref).exists(), f"{d.name} references {ref}, which doesn't exist"
        for other in re.findall(r"\*\*(labview-[\w-]+)\*\*", t):
            assert (REPO / "skills" / other).exists(), f"{d.name} points to a nonexistent skill {other}"
