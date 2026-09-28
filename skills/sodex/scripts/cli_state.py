"""Private local profiles, durable intents/receipts, and process coordination."""

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from decimal import Decimal
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

from eth_account import Account
from eth_utils import to_checksum_address


class InputError(ValueError):
    """A safe, locally generated error message suitable for JSON output."""


def require(condition, message):
    if not condition:
        raise InputError(message)


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def encode(value):
    return json.dumps(plain(value), separators=(",", ":"), allow_nan=False)


def secret(env_name, prompt):
    if env_name:
        require(
            bool(os.environ.get(env_name)),
            "The named secret environment variable is empty.",
        )
        return os.environ[env_name]
    require(
        sys.stdin.isatty(),
        "Use an interactive terminal or a named secret environment variable.",
    )
    return getpass.getpass(prompt)


class State:
    def __init__(self, root):
        self.root = Path(root).expanduser()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        db = self.root / "operations.sqlite3"
        fd = os.open(str(db), os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(db, 0o600)
        self.db = sqlite3.connect(str(db), timeout=10)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, created INTEGER NOT NULL,
                document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, operation_id TEXT NOT NULL,
                time INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS nonces (signer TEXT PRIMARY KEY, value INTEGER NOT NULL);
        """)

    def profile_path(self, name):
        require(
            bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)), "Invalid profile name."
        )
        return self.root / (name + ".json")

    def add_profile(
        self, name, network, owner, account_id, api_key_name, key=None, password=None
    ):
        require(network in ("mainnet", "testnet"), "Choose mainnet or testnet.")
        try:
            owner = to_checksum_address(owner)
        except (ValueError, TypeError):
            raise InputError("Owner must be a valid EVM address.") from None
        require(account_id is None or 0 < account_id < 2**64, "Invalid account ID.")
        profile = {
            "name": name,
            "network": network,
            "owner": owner,
            "account_id": account_id,
            "api_key_name": api_key_name,
            "signer": None,
        }
        if key is not None:
            require(bool(password), "A nonempty keystore password is required.")
            try:
                signer = Account.from_key(key)
                profile["signer"] = signer.address
                require(
                    bool(api_key_name) or signer.address == owner,
                    "A delegated key requires its registered API key name.",
                )
                profile["keystore"] = Account.encrypt(key, password)
            except (ValueError, TypeError) as exc:
                if isinstance(exc, InputError):
                    raise
                raise InputError("Invalid private key.") from None
        path = self.profile_path(name)
        require(not path.exists(), "Profile already exists; choose a new name.")
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(encode(profile) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return self.public_profile(profile)

    def profile(self, name):
        require(bool(name), "Select a local profile with --profile.")
        path = self.profile_path(name)
        require(path.exists(), "Profile does not exist; run profile init first.")
        return json.loads(path.read_text())

    @staticmethod
    def public_profile(profile):
        return {k: v for k, v in profile.items() if k != "keystore"}

    def create(self, document):
        operation_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO operations VALUES (?, 'prepared', ?, ?)",
                (operation_id, int(time.time() * 1000), encode(document)),
            )
            self.event(operation_id, "prepared", document["preflight"])
        return self.get(operation_id)

    def event(self, operation_id, kind, data):
        self.db.execute(
            "INSERT INTO events(operation_id,time,kind,data) VALUES (?,?,?,?)",
            (operation_id, int(time.time() * 1000), kind, encode(data)),
        )

    def transition(self, operation_id, expected, state, data):
        with self.db:
            result = self.db.execute(
                "UPDATE operations SET state=? WHERE id=? AND state=?",
                (state, operation_id, expected),
            )
            require(
                result.rowcount == 1,
                "Operation is no longer executable; inspect or reconcile it.",
            )
            self.event(operation_id, state, data)

    def get(self, operation_id):
        row = self.db.execute(
            "SELECT state,created,document FROM operations WHERE id=?", (operation_id,)
        ).fetchone()
        require(row is not None, "Unknown operation ID.")
        events = self.db.execute(
            "SELECT time,kind,data FROM events WHERE operation_id=? ORDER BY sequence",
            (operation_id,),
        ).fetchall()
        return dict(
            id=operation_id,
            state=row[0],
            created_at=row[1],
            **json.loads(row[2]),
            events=[dict(time=t, kind=k, data=json.loads(d)) for t, k, d in events],
        )

    def recent(self):
        return [
            dict(id=i, state=s, created_at=t)
            for i, s, t in self.db.execute(
                "SELECT id,state,created FROM operations ORDER BY created DESC LIMIT 100"
            )
        ]

    @contextmanager
    def signing_lock(self, profile):
        name = profile["network"] + "-" + profile["signer"].lower()
        fd = os.open(str(self.root / (name + ".lock")), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InputError(
                    "This signer already has a running command; wait and inspect its operation."
                ) from None
            yield

    def next_nonce(self, signer):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT value FROM nonces WHERE signer=?", (signer,)
            ).fetchone()
            value = max(int(time.time() * 1000), row[0] + 1 if row else 0)
            self.db.execute(
                "INSERT OR REPLACE INTO nonces VALUES (?,?)", (signer, value)
            )
        return value
