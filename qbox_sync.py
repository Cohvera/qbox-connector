#!/usr/bin/env python3
"""Push validated Plenion TV exports to Cohvera. Python 3.10+, standard library only."""
import argparse
import hashlib
import json
import logging
import os
import re
import signal
import ssl
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

LOG = logging.getLogger("qbox")
MAX_BYTES = 4_000_000
STOP = threading.Event()

class SyncError(Exception):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.timestamp()
    except (AttributeError, TypeError, ValueError):
        raise SyncError("Ongeldige brondatum") from None


def https_url(value, *, allow_http=False):
    try:
        u = urlsplit(value)
        if u.scheme not in (("https", "http") if allow_http else ("https",)) or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError()
        u.port
    except ValueError:
        raise SyncError("Ongeldig adres: alleen de LAN-bron mag HTTP gebruiken; Cohvera vereist HTTPS") from None
    return value.rstrip("/")


def settings():
    key = os.environ.get("QBOX_IMPORT_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", key):
        raise SyncError("QBOX_IMPORT_API_KEY ontbreekt of is ongeldig")
    source = https_url(os.environ.get("QBOX_SOURCE_URL", "https://www.tomme-energie.lan/projecten-tv"), allow_http=True)
    destination = https_url(os.environ.get("QBOX_PORTAL_URL", ""))
    if urlsplit(destination).path not in ("", "/"):
        raise SyncError("QBOX_PORTAL_URL moet het portaaladres zonder /api zijn")
    try:
        interval = int(os.environ.get("QBOX_POLL_SECONDS", "60"))
        if not 30 <= interval <= 3600:
            raise ValueError()
    except ValueError:
        raise SyncError("QBOX_POLL_SECONDS moet tussen 30 en 3600 liggen") from None
    mode = os.environ.get("QBOX_SOURCE_MODE", "tv")
    if mode not in ("tv", "datahub"):
        raise SyncError("QBOX_SOURCE_MODE moet tv of datahub zijn")
    hub = https_url(os.environ.get("QBOX_DATAHUB_URL", "http://192.168.10.228:3210"), allow_http=True)
    return dict(tv_enabled=os.environ.get("QBOX_TV_ENABLED", "0") == "1", nas_url=https_url(os.environ["QBOX_NAS_URL"], allow_http=True) if os.environ.get("QBOX_NAS_URL") else None, key=key, source=source, destination=destination, interval=interval, mode=mode, hub=hub,
                ca=(os.environ.get("QBOX_SOURCE_CA_FILE") or None) if urlsplit(hub if mode == "datahub" else source).scheme == "https" else None,
                state=Path(os.environ.get("QBOX_STATE_FILE", "/var/lib/cohvera-qbox/state.json")))


def read_json(url, *, ca=None, payload=None, key=None):
    if payload is not None or key is not None:
        https_url(url)  # Never transmit import credentials over HTTP.
    headers = {"Accept": "application/json", "User-Agent": "Cohvera-Qbox/1"}
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        if len(body) > MAX_BYTES:
            raise SyncError("Export is te groot")
        headers.update({"Content-Type": "application/json", "X-Qbox-Key": key})
    try:
        context = ssl.create_default_context(cafile=ca if urlsplit(url).scheme == "https" else None)
        # Direct connection; never send the key through an environment-configured proxy.
        opener = build_opener(ProxyHandler({}), NoRedirect, HTTPSHandler(context=context))
        with opener.open(Request(url, data=body, headers=headers), timeout=90 if body else 15) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SyncError("Antwoord is te groot")
        result = json.loads(raw)
        if not isinstance(result, (dict, list)):
            raise SyncError("Onverwacht antwoordformaat")
        return result
    except HTTPError as error:
        # Do not log request headers, source data, secrets or raw server responses.
        raise SyncError(f"HTTP {error.code} bij {'Cohvera' if body else 'de LAN-bron'}") from None
    except (URLError, OSError, ValueError):
        raise SyncError("Verbinding, certificaat of JSON ongeldig; controleer netwerk en CA-configuratie") from None


def validate(data, status, now=None):
    now = time.time() if now is None else now
    if (data.get("schema_version") != 2 or data.get("source_kind") != "central_datahub" or
        data.get("source_system") != "PLENION" or data.get("live") is not True or
        status.get("state") != "SUCCESS" or status.get("source_kind") != "central_datahub" or
        not data.get("batch_id") or data.get("batch_id") != status.get("batch_id") or
        not data.get("snapshot_id") or data.get("snapshot_id") != status.get("hub_snapshot_id")):
        raise SyncError("Geen overeenstemmende, succesvolle Central Data Hub-export")
    observed = timestamp(data.get("source_observed_at"))
    if timestamp(status.get("source_observed_at")) != observed:
        raise SyncError("Bronstand en importstatus komen niet overeen")
    valid_until = timestamp(data.get("valid_until"))
    if observed > now + 300 or now - observed >= 86400 or valid_until <= now or valid_until > observed + 86400:
        raise SyncError("Bronstand is verlopen of heeft een ongeldige datum")
    heartbeat = timestamp(status.get("heartbeat_at"))
    if heartbeat > now + 300 or now - heartbeat > 180:
        raise SyncError("Bronworker heeft geen actuele heartbeat")
    projects = data.get("projects")
    if not isinstance(projects, list) or len(projects) > 10000:
        raise SyncError("Ongeldige projectlijst")
    seen = set()
    for p in projects:
        if not isinstance(p, dict):
            raise SyncError("Ongeldig project")
        number = p.get("number")
        if not isinstance(number, str) or not re.fullmatch(r"\d{1,60}", number) or number in seen:
            raise SyncError("Ongeldig of dubbel projectnummer")
        seen.add(number)
        if (p.get("status_label") != "07 - In Uitvoering" or p.get("evidence") != "central_datahub" or
            p.get("active") is not True or p.get("is_current") is not True or p.get("closed") is not False):
            raise SyncError("Project voldoet niet aan de afgesproken bronselectie")
        for field, limit in (("customer", 300), ("description", 2000), ("planned", 10)):
            if not isinstance(p.get(field), str) or len(p[field]) > limit:
                raise SyncError("Ongeldig projectveld")
        if p["planned"]:
            try:
                if datetime.strptime(p["planned"], "%Y-%m-%d").strftime("%Y-%m-%d") != p["planned"]:
                    raise ValueError()
            except ValueError:
                raise SyncError("Ongeldige plandatum") from None
    # Forward only the agreed contract, never arbitrary extra source fields.
    return {**{k:data[k] for k in ("schema_version","source_kind","source_system","live","snapshot_id","batch_id","source_observed_at","valid_until")},
            "projects":[{k:p[k] for k in ("number","customer","description","planned","status_label","evidence","active","is_current","closed")} for p in sorted(projects,key=lambda p:p["number"])]}


def flag(value):
    if type(value) not in (bool, int) or value not in (0, 1):
        raise SyncError("Onbekende projectcheckbox; broncontrole vereist")
    return bool(value)


def read_datahub(config):
    base = config["hub"]
    def read(path):
        return read_json(base + path, ca=config["ca"])
    before = read("/api/v1/health")
    evidence = read("/api/v1/tv/projects")
    if not isinstance(before, dict) or not isinstance(evidence, dict):
        raise SyncError("Ongeldige Data Hub-metadata")
    sync = before.get("sync") or {}
    snapshot = sync.get("snapshot_id")
    if (before.get("service") != "ok" or sync.get("status") != "success" or
        not snapshot or evidence.get("snapshot_id") != snapshot or
        evidence.get("source_kind") != "central_datahub" or evidence.get("source_system") != "PLENION" or
        evidence.get("complete") is not True):
        raise SyncError("Geen actuele consistente Data Hub-snapshot")
    observed = timestamp(evidence.get("source_observed_at"))
    if observed > time.time()+300 or time.time()-observed >= 86400:
        raise SyncError("Data Hub-bronstand is verlopen")
    counts = before.get("counts", [])
    expected = next((c.get("count") for c in counts if c.get("entity") == "projects"), None)
    if type(expected) is not int or not 0 <= expected <= 10000:
        raise SyncError("Ongeldig projecttotaal in Data Hub")
    records = []
    for offset in range(0, expected, 500):
        page = read(f"/api/v1/projects?limit=500&offset={offset}&sort=id")
        if not isinstance(page, list) or len(page) != min(500, expected-offset):
            raise SyncError("Onvolledige projectpagina; niets verstuurd")
        records.extend(page)
    after = read("/api/v1/health")
    if (not isinstance(after, dict) or after.get("service") != "ok" or
        after.get("sync") != before.get("sync") or after.get("counts") != counts):
        raise SyncError("Bron veranderde tijdens uitlezen; volgende poging opnieuw")
    ids, numbers, projects = set(), set(), []
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in ids:
            raise SyncError("Dubbele of ongeldige bronrecord-ID")
        ids.add(row["id"])
        if not flag(row.get("is_current")):
            continue
        number = row.get("project_number")
        if not isinstance(number, str) or not re.fullmatch(r"\d{1,60}", number) or number in numbers:
            raise SyncError("Meer dan één huidige versie of ongeldig projectnummer")
        numbers.add(number)
        label = row.get("status_label")
        if not isinstance(label, str) or not label.strip() or len(label)>100:
            raise SyncError("Projectstatus ontbreekt")
        project = dict(number=number,customer=row.get("customer_name"),description=row.get("description"),planned=row.get("planned_date"),status_label=label,evidence="central_datahub",active=flag(row.get("is_active")),closed=flag(row.get("is_closed")),is_current=True,source_id=row["id"])
        # Reuse the TV validator for shared text/date validation, with a synthetic valid scope.
        common = dict(project, status_label="07 - In Uitvoering", active=True, closed=False)
        metadata = dict(schema_version=2,source_kind="central_datahub",source_system="PLENION",live=True,snapshot_id=snapshot,batch_id=snapshot,source_observed_at=evidence["source_observed_at"],valid_until=datetime.fromtimestamp(observed+86400,timezone.utc).isoformat(),projects=[common])
        validate(metadata,dict(state="SUCCESS",source_kind="central_datahub",batch_id=snapshot,hub_snapshot_id=snapshot,source_observed_at=evidence["source_observed_at"],heartbeat_at=datetime.now(timezone.utc).isoformat()))
        projects.append(project)
    return dict(schema_version=3,scope="ALL_CURRENT_PROJECTS",complete=True,source_kind="central_datahub",source_system="PLENION",live=True,snapshot_id=snapshot,batch_id=snapshot,source_observed_at=evidence["source_observed_at"],valid_until=datetime.fromtimestamp(observed+86400,timezone.utc).isoformat(),projects=sorted(projects,key=lambda p:p["number"]))


def sync_once(config, dry_run=False, force=False):
    if config.get("mode", "tv") == "datahub":
        payload = read_datahub(config)
    else:
        data = read_json(config["source"] + "/tv-project-data.json", ca=config["ca"])
        status = read_json(config["source"] + "/tv-refresh-status.json", ca=config["ca"])
        if not isinstance(data, dict) or not isinstance(status, dict):
            raise SyncError("Ongeldige TV-export")
        payload = validate(data, status)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if dry_run:
        LOG.info("Bron geldig: %d projecten; niets verstuurd", len(payload["projects"]))
        return "validated"
    try:
        state = json.loads(config["state"].read_text())
    except (OSError, ValueError):
        state = {}
    # Reconfirm every six hours, also after key/destination changes or loss of local state.
    target = hashlib.sha256((config["destination"] + config["key"]).encode()).hexdigest()
    if not force and isinstance(state, dict) and state.get("digest") == digest and state.get("target") == target and isinstance(state.get("sent_at"), (int,float)) and 0 <= time.time()-state["sent_at"] < 21600:
        return "unchanged"
    result = read_json(config["destination"] + "/api/integrations/qbox/plenion/projects", payload=payload, key=config["key"])
    if result.get("accepted") is not True or result.get("snapshotId") != payload["snapshot_id"] or result.get("count") != len(payload["projects"]):
        raise SyncError("Cohvera heeft de import niet bevestigd")
    config["state"].parent.mkdir(parents=True, exist_ok=True)
    temporary = config["state"].with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(digest=digest,target=target,sent_at=time.time())))
    temporary.chmod(0o600)
    temporary.replace(config["state"])
    LOG.info("Cohvera bevestigt %d projecten%s", result["count"], " (reeds verwerkt)" if result.get("duplicate") else "")
    return "accepted"


def sync_tv(config, dry_run=False):
    if not config.get("tv_enabled"):
        return
    payload = {"fleet": None, "nas": None}
    try:
        fleet = read_json(config["source"] + "/tv-vehicles.json", ca=config["ca"])
        if not isinstance(fleet, dict) or not isinstance(fleet.get("vehicles"), list):
            raise SyncError("Ongeldige voertuigenbron")
        payload["fleet"] = {k: fleet.get(k) for k in ("schema_version", "source_kind", "snapshot_id", "source_observed_at")}
        payload["fleet"]["vehicles"] = [{k: row.get(k) for k in ("plate", "name", "next_inspection", "source_id")} for row in fleet["vehicles"] if isinstance(row, dict)]
    except SyncError:
        LOG.warning("Voertuigenbron niet beschikbaar; tv toont geen bevestigde voertuigen")
    if config.get("nas_url"):
        try:
            nas = read_json(config["nas_url"], ca=config["ca"])
            if not isinstance(nas, dict):
                raise SyncError("Ongeldige NAS-bron")
            latest = nas.get("latest") or {}
            payload["nas"] = {"status": nas.get("status"), "loggingOk": nas.get("loggingOk"),
                "latest": {k: latest.get(k) for k in ("checkedAt", "cpu", "memory", "disk")},
                "history": [{k: row.get(k) for k in ("checkedAt", "cpu", "ram")} for row in nas.get("history", [])[-400:] if isinstance(row, dict)]}
        except (SyncError, TypeError, AttributeError):
            LOG.warning("NAS-bron niet beschikbaar; tv toont geen actuele NAS-meting")
    if dry_run:
        LOG.info("TV-bronnen gelezen: voertuigen %s, NAS %s; niets verstuurd", payload["fleet"] is not None, payload["nas"] is not None)
        return
    result = read_json(config["destination"] + "/api/integrations/qbox/tv", payload=payload, key=config["key"])
    if not isinstance(result, dict) or result.get("accepted") is not True:
        raise SyncError("Cohvera heeft de tv-gegevens niet bevestigd")
    LOG.info("Cohvera bevestigt tv-gegevens: voertuigen %s, NAS %s", payload["fleet"] is not None, payload["nas"] is not None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Eén poging, exitcode 1 bij fout")
    parser.add_argument("--dry-run", action="store_true", help="Bron controleren; niets versturen of opslaan")
    parser.add_argument("--force", action="store_true", help="Met --once: verstuur ook een ongewijzigde bronstand opnieuw")
    args = parser.parse_args()
    if args.force and not args.once:
        parser.error("Gebruik --force alleen samen met --once")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = settings()
    except SyncError as error:
        LOG.error("%s", error)
        return 1
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    failures = 0
    while not STOP.is_set():
        try:
            sync_once(config, args.dry_run, args.force)
            failures = 0
        except (SyncError, OSError) as error:
            # OS errors can contain file paths but not credentials or payloads.
            LOG.error("Synchronisatie mislukt: %s", error if isinstance(error, SyncError) else "Lokale status kon niet opgeslagen worden")
            failures += 1
        try:
            sync_tv(config, args.dry_run)
        except (SyncError, OSError) as error:
            LOG.error("TV-synchronisatie mislukt: %s", error if isinstance(error, SyncError) else "Lokale fout")
            failures += 1
        if args.once or args.dry_run:
            return 1 if failures else 0
        STOP.wait(min(900, config["interval"] * 2 ** min(failures, 4)))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
