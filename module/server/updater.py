import datetime
import subprocess
from typing import Tuple

from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager
from deploy.utils import DEPLOY_CONFIG
from module.logger import logger
from module.base.retry import retry
from module.server.config import DeployConfig
from deploy.package_release import (
    PACKAGE_ROOT, has_own_git_repository, is_fixed_package, read_package_release,
)


DEFAULT_GIT_TIMEOUT = 5
DEFAULT_FETCH_TIMEOUT = 15
FETCH_RETRY = 2


class Updater(DeployConfig, GitManager, PipManager):
    def __init__(self, file=DEPLOY_CONFIG):
        super().__init__(file=file)
        self.state = 0

    @property
    def delay(self):
        self.read()
        return int(self.CheckUpdateInterval) * 60

    @property
    def schedule_time(self):
        self.read()
        t = self.AutoRestartTime
        if t is not None:
            return datetime.time.fromisoformat(t)
        else:
            return None

    def execute_command(self, command, timeout=DEFAULT_GIT_TIMEOUT) -> subprocess.CompletedProcess | None:
        if is_fixed_package() or not has_own_git_repository(self.git):
            logger.warning('Git updates require an OAS repository in the OAS root')
            return None
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf8",
                errors="replace",
                shell=True,
                cwd=PACKAGE_ROOT,
                timeout=timeout,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"Git command timeout after {timeout}s: {command}")
            return None
        except OSError as e:
            logger.warning(f"Git command failed: {command}, error: {e}")
            return None

    def execute_output(self, command, timeout=DEFAULT_GIT_TIMEOUT) -> str:
        result = self.execute_command(command, timeout=timeout)
        if result is None:
            return ""
        if result.returncode:
            logger.warning(f"Git command failed: {command}, stderr: {result.stderr.strip()}")
            return ""
        return result.stdout

    def get_commit(self, revision="", n=1, short_sha1=False) -> Tuple:
        """
        Return:
            (sha1, author, isotime, message,)
        """
        ph = "h" if short_sha1 else "H"

        log = self.execute_output(
            f'"{self.git}" --no-pager log {revision} --pretty=format:"%{ph}---%an---%ad---%s" --date=iso -{n}'
        )

        if not log:
            return ('unknown', 'unknown', '', '未找到当前 OAS 目录的 Git 仓库') if n == 1 else []

        logs = log.split("\n")
        logs = list(map(lambda log: tuple(log.split("---")), logs))

        if n == 1:
            return logs[0]
        else:
            return logs

    def current_branch(self) -> str:
        if is_fixed_package():
            return read_package_release()['branch']
        return self.Branch

    def current_commit(self) -> str:
        if is_fixed_package():
            return self.get_update_info()['current_commit']
        return self.get_commit()

    def latest_commit(self) -> str:
        if is_fixed_package():
            return self.get_update_info()['latest_commit']
        source = "origin"
        return self.get_commit(f"{source}/{self.Branch}")

    def fetch_remote(self) -> bool:
        if is_fixed_package():
            logger.info('Fixed deployment package: Git fetch is disabled')
            return False
        if not self.AutoUpdate:
            logger.info("AutoUpdate is disabled, skip git fetch")
            return False

        source = "origin"
        for _ in range(FETCH_RETRY):
            result = self.execute_command(
                f'"{self.git}" fetch {source} {self.Branch}',
                timeout=DEFAULT_FETCH_TIMEOUT,
            )
            if result is not None and result.returncode == 0:
                return True
            if result is not None:
                logger.warning(f"Git fetch failed: {result.stderr.strip()}")

        logger.warning("Git fetch failed")
        return False

    def revision_distance(self, left: str, right: str) -> tuple[int, int] | None:
        log = self.execute_output(
            f'"{self.git}" rev-list --left-right --count {left}...{right}'
        )
        if not log:
            return None

        try:
            ahead, behind = log.split()
            return int(ahead), int(behind)
        except ValueError:
            logger.warning(f"Unexpected git rev-list output: {log.strip()}")
            return None

    def get_update_info(self) -> dict:
        if is_fixed_package():
            try:
                release = read_package_release()
                commit = (
                    release['commit'], release['repository'], release['built_at'],
                    '固定部署包 ' + release['release'] + '（离线版本；不查询远程仓库）',
                )
                branch = release['branch']
                repository = release['repository']
                error = ''
            except (OSError, ValueError) as exc:
                logger.error(f'Invalid fixed package manifest: {exc}')
                commit = ('unknown', 'unknown', '', '固定部署包版本清单无效，请重新解压完整部署包')
                branch, repository, error = '', '', str(exc)
            # Keep all existing OASX fields as arrays of strings, never null.
            return {
                'is_update': False, 'branch': branch, 'current_commit': commit,
                'latest_commit': commit, 'commit': [commit],
                'repository': repository, 'update_mode': 'fixed-package',
                'remote_checked': False, 'error': error,
            }
        is_update = False
        source = "origin"
        remote_revision = f"{source}/{self.Branch}"

        if self.fetch_remote():
            distance = self.revision_distance("HEAD", remote_revision)
            if distance is None:
                is_update = False
            else:
                ahead, behind = distance
                is_update = not ahead and bool(behind)
                if ahead:
                    logger.info("Local branch has commits not in upstream, skip update")
                logger.info("New update available" if is_update else "No update")

        commits = self.get_commit(remote_revision, n=15)
        latest_commit = commits[0] if commits and isinstance(commits, list) else commits
        return {
            'is_update': is_update,
            'branch': self.current_branch(),
            'current_commit': self.current_commit(),
            'latest_commit': latest_commit,
            'commit': commits,
        }

    def execute_pull(self) -> bool:
        if is_fixed_package() or not has_own_git_repository(self.git):
            logger.info('Fixed package or missing local OAS Git repository: pull is disabled')
            return False
        if not self.AutoUpdate:
            logger.info("AutoUpdate is disabled, skip git pull")
            return False

        source = "origin"
        for _ in range(FETCH_RETRY):
            if self.execute(
                    f'"{self.git}" pull {source} {self.Branch} --no-rebase', allow_failure=True
            ):
                break
        else:
            logger.warning("Git fetch failed")
            return False


if __name__ == "__main__":
    updater = Updater()
    print(updater.latest_commit())
