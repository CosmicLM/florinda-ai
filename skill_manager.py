"""skill_manager.py — lets Florinda save and reuse its own small scripts as "skills".

WHY this exists: the same reliability motivation behind hyprland_bridge.py and
app_launcher.py — instead of re-deriving a raw multi-step shell command from
scratch every time a similar request recurs, the AI can write it once as a
named skill and invoke it by name from then on. A CLI wrapper the AI reliably
invokes as its COMMAND field, same pattern as every other bridge in this
codebase.

WHY create/list are auto-safe but run is not (see flora_daemon.py's
_AUTO_SAFE_SKILL_MANAGER_ACTIONS): saving a new skill to disk is inert until
something actually runs it — the user's "let it self-improve autonomously"
decision covers writing new code, not granting it the standing execution
trust reserved for hyprland_bridge.py's fixed, pre-verified dispatch set. A
skill is arbitrary AI-authored code, so `run` still goes through the normal
confirm/trust-session gate like any other shell command.
"""
import ast
import subprocess
import sys
import tempfile
from pathlib import Path
from py_compile import PyCompileError, compile as py_compile

from pyflakes.api import check as pyflakes_check
from pyflakes.reporter import Reporter

DEFAULT_SKILLS_DIR = Path("./skills")


class SkillManagerError(Exception):
    """Raised when a skill can't be found, created, or run."""


class SkillManager:
    """Creates, lists, and runs small AI-authored Python scripts under skills_dir."""

    def __init__(self, skills_dir: Path = DEFAULT_SKILLS_DIR) -> None:
        self._skills_dir = skills_dir

    def create_skill(self, name: str, source: str) -> Path:
        """Validates `source` before ever writing it to disk, in two passes:
        py_compile catches syntax errors; pyflakes catches undefined names
        (typo'd variables, a missing import) that py_compile can't see since
        those are only real bugs at RUN time, not syntax errors. Observed
        live: skills saved with `import pathlbo` used as `expanduser` (never
        imported) and `vidget_cmd` assigned but `widget_cmd` referenced —
        both syntactically valid, both broken, both would have been caught
        here."""
        self._validate_name(name)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp_file:
            tmp_file.write(source)
            tmp_path = Path(tmp_file.name)
        try:
            py_compile(str(tmp_path), doraise=True)
        except PyCompileError as error:
            raise SkillManagerError(f"skill {name!r} failed to compile: {error}") from error
        finally:
            tmp_path.unlink(missing_ok=True)

        undefined_names = self._check_undefined_names(source, name)
        if undefined_names:
            raise SkillManagerError(
                f"skill {name!r} references undefined names — likely a missing import or a "
                f"typo'd variable name: {'; '.join(undefined_names)}"
            )

        self._skills_dir.mkdir(parents=True, exist_ok=True)
        skill_path = self._skills_dir / f"{name}.py"
        skill_path.write_text(source)
        return skill_path

    @staticmethod
    def _check_undefined_names(source: str, name: str) -> list[str]:
        messages: list[str] = []

        class _CollectingReporter(Reporter):
            def __init__(self) -> None:
                super().__init__(sys.stderr, sys.stderr)

            def flake(self, message) -> None:
                messages.append(str(message))

        pyflakes_check(source, f"{name}.py", _CollectingReporter())
        return [m for m in messages if "undefined name" in m]

    def list_skills(self) -> list[tuple[str, str]]:
        """Returns [(name, description), ...] — description is each skill
        file's module docstring, read via ast (no need to import/execute
        untrusted code just to list what's available)."""
        skills = []
        if not self._skills_dir.exists():
            return skills
        for path in sorted(self._skills_dir.glob("*.py")):
            description = self._read_docstring(path) or "(no description)"
            skills.append((path.stem, description))
        return skills

    def run_skill(self, name: str, args: list[str]) -> str:
        self._validate_name(name)
        skill_path = self._skills_dir / f"{name}.py"
        if not skill_path.exists():
            raise SkillManagerError(f"no skill named {name!r}")
        result = subprocess.run(
            [sys.executable, str(skill_path), *args],
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout
        if result.returncode != 0:
            output += f"\n[skill exited {result.returncode}]\n{result.stderr}"
        return output.strip()

    @staticmethod
    def _validate_name(name: str) -> None:
        """WHY: name becomes a filename inside skills_dir — reject anything
        that could escape it (path separators, `..`) rather than trusting
        the AI to never produce one."""
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            raise SkillManagerError(f"invalid skill name: {name!r}")

    @staticmethod
    def _read_docstring(path: Path) -> str | None:
        try:
            tree = ast.parse(path.read_text())
            return ast.get_docstring(tree)
        except (OSError, SyntaxError):
            return None


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Florinda AI's autonomous skill manager CLI")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("list")

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("name")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("name")
    run_parser.add_argument("args", nargs="*")

    args = parser.parse_args()
    manager = SkillManager()
    try:
        if args.action == "list":
            skills = manager.list_skills()
            if not skills:
                print("No skills saved yet.")
            for skill_name, description in skills:
                print(f"{skill_name}: {description}")
        elif args.action == "create":
            source = sys.stdin.read()
            skill_path = manager.create_skill(args.name, source)
            print(f"Saved skill: {skill_path}")
        elif args.action == "run":
            print(manager.run_skill(args.name, args.args))
    except SkillManagerError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
