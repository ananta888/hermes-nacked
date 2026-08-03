# Hermes Naked

Hermes läuft hier **deny by default** in Docker. Der Startzustand ist reiner
Chat: keine model-facing Tools, keine Shell, keine Skills, kein Memory, keine
Plugins/MCPs und keine automatische Projektanweisung aus `AGENTS.md`,
`SOUL.md`, `.hermes.md`, `CLAUDE.md` oder `.cursorrules`.

„Reiner Chat“ bedeutet dabei: keine externen Fähigkeiten und kein persönlicher
Kontext. Der fest in Hermes eingebaute Basis-Systemprompt bleibt erhalten; er
ist Teil des Agent-Kerns und keine separat geladene Datei.

Die Rechte sind keine aufeinander aufbauenden Profile. Jeder Schalter wird
separat gesetzt und kann separat wieder entfernt werden.

## Schnellstart

```bash
cp .env.example .env

./hermesctl init
./hermesctl build
./hermesctl model
./hermesctl status
./hermesctl chat
```

`./hermesctl model` ist der offizielle interaktive Hermes-Assistent für
Provider, Anmeldung und Modellwahl. Die Auswahl wird im isolierten
`runtime/state` gespeichert und gilt ab der nächsten Sitzung. Provider- und
Modellwahl sind Operator-Aktionen und werden weder dem Agenten noch dem
`hermesctl`-MCP als Tool angeboten.

Allgemeine Provider-Zugangsdaten lassen sich auch separat verwalten:

```bash
./hermesctl auth
```

Der anfängliche Status muss `Capabilities: none (pure chat)` und
`Hermes toolsets: none` zeigen.

Das Hermes-Basisimage ist bewusst per Digest gepinnt. Ein Upgrade erfolgt
absichtlich über `HERMES_IMAGE` in `.env` und anschließendem
`./hermesctl build && ./hermesctl verify`, nicht unbemerkt über `latest`.

### Python als Standardlaufzeit

Alle vom Projekt gebauten Container enthalten ausdrücklich `python`,
`python3`, `pip3` und das `venv`-Modul. Das gilt für den Hermes-Controller,
die festen Codex-/Claude-/OpenCode-Worker und alle generischen Agenten, weil
diese dasselbe Worker-Basisimage verwenden. Die konkrete Python-Version folgt
dem jeweils gepinnten Debian-Basisimage.

Die standardmäßige Command-Sandbox verwendet weiterhin das gepinnte
`python:3.13-slim-bookworm`-Image. Wer `HERMES_SANDBOX_IMAGE` überschreibt,
muss Python im eigenen Ersatzimage selbst bereitstellen. Die installierte
Laufzeit erteilt dem Modell keine Datei-, Shell- oder Netzwerkrechte; dafür
gelten unverändert die separat aktivierten Capabilities.

## Modelle einfach konfigurieren

Für alle Provider beginnt die Auswahl gleich:

```bash
./hermesctl model
```

Danach im Assistenten:

- **LM Studio:** `LM Studio` wählen. Als Base URL innerhalb des Containers
  `http://host.docker.internal:1234/v1` verwenden. In LM Studio muss der
  lokale Server laufen, das Modell geladen und der Zugriff vom Docker-Hostnetz
  erlaubt sein. Ohne aktivierte LM-Studio-Authentifizierung kann der Key leer
  bleiben.
- **Ollama:** `Custom endpoint` wählen. Base URL ist
  `http://host.docker.internal:11434/v1`, der API-Key kann lokal leer bleiben;
  anschließend eines der in Ollama installierten Modelle wählen. Ollama bindet
  standardmäßig nur an `127.0.0.1`; für den Zugriff aus Docker muss es etwa mit
  `OLLAMA_HOST=0.0.0.0:11434` lauschen. Das sollte durch eine lokale Firewall
  weiterhin auf den eigenen Rechner begrenzt werden.
- **Anthropic API:** `Anthropic` und anschließend API-Key wählen. Das ist
  nutzungsabhängig abgerechnete API-Nutzung und nicht im Claude-Monatsabo
  enthalten.

Die Docker-Compose-Datei richtet auf Linux dafür ausschließlich im
Hermes-Controller `host.docker.internal` ein. Das ist für Modell-Inferenz
nötig, erteilt dem Modell aber keine Tools. Der zweite Command-Sandbox-
Container bleibt ohne `shell-network` weiterhin netzlos.

### Anthropic ohne API-Key

Der OAuth-Ablauf ist als Operator-Kommando verfügbar:

```bash
./hermesctl anthropic-login
./hermesctl model
```

Dabei wird im Terminal ein Link ausgegeben. Nach der Anmeldung zeigt Anthropic
einen Code an, der zurück ins Terminal kopiert wird; es ist deshalb keine
Callback-Portfreigabe am Docker-Container nötig.

Wichtig ist die Abgrenzung: Claude Code selbst kann laut Anthropic ein
**Claude-Pro- oder Claude-Max-Abo** verwenden. Hermes dokumentiert für seinen
eigenen Anthropic-OAuth-Zugriff derzeit jedoch nur **Claude Max plus separat
gekaufte Extra-Usage-Credits**. Hermes belastet ausschließlich diese
Zusatz-Credits, nicht das im Max-Abo enthaltene Basiskontingent; mit Claude Pro
funktioniert dieser Weg laut Hermes nicht. Ein normales Monatsabo lässt sich
damit also nicht als Flatrate für Hermes nutzen. Ohne Max plus Extra Usage
bleibt für Hermes der reguläre Anthropic-API-Key.

Referenzen:

- [Hermes: Provider und Anthropic OAuth](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- [Anthropic: Claude Code mit Pro oder Max](https://support.anthropic.com/en/articles/11145838-using-claude-code-with-your-max-plan)
- [Anthropic: Pro enthält keine API-Nutzung](https://support.anthropic.com/en/articles/8325606-what-is-the-pro-plan)
- [Ollama: Zugriff aus Docker über `host.docker.internal`](https://docs.ollama.com/integrations/n8n)
- [Ollama: `OLLAMA_HOST` und Netzwerkfreigabe](https://docs.ollama.com/faq)
- [LM Studio: lokalen Server starten](https://lmstudio.ai/docs/developer/core/server)

## Eine gemeinsame Rechte-Oberfläche

Hermes, Codex CLI, Claude Code und OpenCode lassen sich mit denselben fünf
Benutzerbegriffen steuern:

```bash
# Aktuellen Null-/Rechtestand eines Ziels prüfen
./hermesctl access hermes status
./hermesctl access codex status

# Die tatsächliche technische Abbildung und Alternativen ansehen
./hermesctl access codex explain

# Genau einem Ziel einzelne Rechte geben
./hermesctl access claude enable tool-use
./hermesctl access claude enable commandline
./hermesctl access claude enable skills agents-md

# Nur dieses Ziel zurücksetzen
./hermesctl access claude reset
```

Gültige Ziele sind `hermes`, `codex`, `claude` und `opencode`. Die gemeinsamen
Schalter sind `tool-use`, `commandline`, `skills`, `agents-md` und
`claude-md`. Intern bildet der Adapter sie auf die tatsächlich vorhandenen
Sicherheitsmechanismen ab. Er markiert jede Zeile:

- `[native]`: Die Ziel-CLI besitzt eine passende getrennte Oberfläche.
- `[controlled]`: Hermes Naked injiziert ausschließlich eine geprüfte,
  read-only Quelle; die dynamische native Discovery bleibt aus.
- `[special]`: Die Ziel-CLI kann das gemeinsame Konzept technisch nicht exakt
  abbilden. Direkt darunter steht ein `[alternative]`-Pfad.

Besonders wichtig ist Codex: Es hat kein getrenntes natives File-Tool und sein
inneres `bubblewrap` kann im mit `cap_drop: [ALL]` gehärteten Worker keine
Namespace initialisieren. Deshalb bleibt das Shell-Tool beim Legacy-Worker
für `tool-use` allein aus und wird erst mit `tool-use+commandline` aktiviert;
dann bildet ausschließlich der äußere Docker-Container die Sandbox. Generische
Codex-Instanzen verlangen aus demselben Grund das ausdrückliche Bündel
`inspect+edit+commandline+network`. Wer keine Kommandos erlauben will, lässt
Codex model-only oder verwendet Claude/OpenCode für eine native Trennung.

Bei allen Workern benötigt `commandline` ein bereits aktives `tool-use`; beide
müssen ausdrücklich genannt werden, wenn sie in einem Schritt freigegeben
werden. Bei Hermes sind Datei-Tools und Commandline dagegen wirklich
unabhängig. `skills` bedeutet bei Workern kontrolliert injizierte, zuvor vom
Operator geprüfte `SKILL.md`-Inhalte – keine dynamische Plugin-/Skill-Suche.

Die bisherigen Befehle `./hermesctl enable ...` und
`./hermesctl worker ...` bleiben als Experten- und Kompatibilitätsoberfläche
erhalten. Sie werden außerdem für Hermes-spezifische Rechte wie `web`,
`planning`, `shell-network` oder einzelne Worker-Zugriffswege benötigt.

## Rechte einzeln schalten

```bash
# Datei-Tools: nur runtime/workspace, lesen und schreiben
./hermesctl enable files

# Commandline: separater, gehärteter und zunächst netzloser Container
./hermesctl enable commandline

# Skills: Liste/Ansicht/Verwaltung; weiterhin keine Bundled Skills
./hermesctl enable skills

# Hermes-Kontexte unabhängig: agents-md ist Alias für orchestrator
./hermesctl enable agents-md
./hermesctl enable claude-md

# Webzugriff unabhängig aktivieren
./hermesctl enable web

# Shell-Netzwerk ist ein eigener Schalter
./hermesctl enable shell-network

# Einzelne Rechte wieder entziehen
./hermesctl disable web skills

# Zurück auf komplett eingeschränkt
./hermesctl reset
```

Das Deaktivieren von `skills` löscht installierte Skills nicht; es entfernt
ihre Tools, ihren Prompt-Index und ihren Sandbox-Mount. Damit kann man das Recht
später wieder einschalten, ohne Datenverlust.

Die mit diesem Projekt ausgelieferten Skills `hermesctl-direct`,
`hermesctl-mcp`, `codex-worker`, `claude-worker`, `opencode-worker`,
`agents-direct` und `agents-mcp` werden
in den persistenten Hermes-State synchronisiert.
Installiert bedeutet nicht freigegeben: Ohne `skills` erscheinen sie weder im
Skill-Index noch als Skill-Tools.

Alle Schalter zeigt `./hermesctl capabilities`. `tools` ist ein Alias für
`files`, `shell` und `terminal` sind Aliase für `commandline`; `agents-md` und
`AGENTS.md` sind Aliase für `orchestrator`.

Skills können durch den Operator installiert werden, ohne sie dem Agenten
sofort freizugeben:

```bash
./hermesctl skills list
./hermesctl skills install <quelle>
./hermesctl enable skills
```

## Hermes als eigener Orchestrator

Die Orchestrator-Anweisungen in `AGENTS.md`, der optionale `CLAUDE.md`-Kontext,
die Skills und die zwei Steuerungswege sind getrennte Berechtigungen. Der
Nullzustand lädt keine davon.

Direkter Weg über das eingeschränkte `hermesctl` in der netzlosen
Command-Sandbox:

```bash
./hermesctl enable orchestrator skills commandline hermesctl-direct
./hermesctl chat
```

MCP-Weg ohne allgemeine Commandline-Berechtigung:

```bash
./hermesctl reset
./hermesctl enable orchestrator skills hermesctl-mcp
./hermesctl chat
```

Beide Wege gleichzeitig:

```bash
./hermesctl enable orchestrator skills commandline hermesctl-direct hermesctl-mcp
```

`orchestrator` beziehungsweise sein Alias `agents-md` lädt nur die
projektgebundene, geschützte `AGENTS.md`; `claude-md` lädt unabhängig die
geschützte Root-`CLAUDE.md`. Beide können gleichzeitig aktiv sein, geben aber
selbst kein Tool frei. Anders als bei Hermes' regulärer Priorität „erste Datei
gewinnt“ injiziert die Hülle dann beide explizit. `hermesctl-direct` benötigt
`skills` und `commandline`.
`hermesctl-mcp` benötigt `skills` und registriert ausschließlich:

- `mcp__hermesctl__access_status`
- `mcp__hermesctl__access_explain`
- `mcp__hermesctl__access_enable`
- `mcp__hermesctl__access_disable`
- `mcp__hermesctl__access_reset`
- `mcp__hermesctl__status`
- `mcp__hermesctl__list_capabilities`
- `mcp__hermesctl__enable`
- `mcp__hermesctl__disable`
- `mcp__hermesctl__reset`
- `mcp__hermesctl__worker_rights`
- `mcp__hermesctl__worker_enable`
- `mcp__hermesctl__worker_disable`
- `mcp__hermesctl__worker_reset`
- `mcp__hermesctl__agent_list`
- `mcp__hermesctl__agent_rights`
- `mcp__hermesctl__agent_explain`
- `mcp__hermesctl__agent_grant`
- `mcp__hermesctl__agent_revoke`
- `mcp__hermesctl__agent_reset`

Beide `hermesctl-*`-Capabilities sind Meta-Berechtigungen: Ein damit
ausgestattetes Modell kann die Policy für die nächste Sitzung verändern. Eine
laufende Sitzung erhält dadurch keine neuen Tools; nach einer Änderung muss sie
beendet und neu gestartet werden. `reset` entfernt auch diese Meta-Rechte und
den Orchestrator-Kontext.

## Beliebig viele Agenten und Teams

Die drei festen Worker bleiben als einfache Kompatibilitätsoberfläche erhalten.
Für mehrere Instanzen derselben Engine ist die generische Registry vorgesehen.
Eine Instanz besitzt getrennt:

- Engine, Rolle und fein abgestuftes Rechteprofil,
- persistenten State, privaten Workspace und geschützten Kontext,
- eigenen Docker-Container und Unix-Socket,
- eine Credential-Broker-Zuordnung auf genau einen persistenten CLI-Home.

Ein einzelner Agent ist damit schnell aufgesetzt:

```bash
./hermesctl agent create backend --engine codex --role implementierung
./hermesctl agent explain backend
./hermesctl agent grant backend inspect edit commandline network skills agents-md
./hermesctl agent build backend
./hermesctl agent login backend       # oder lokal: ./hermesctl login-ui
./hermesctl agent clone backend /pfad/zum/projekt
./hermesctl agent start backend
./hermesctl agent status backend
./hermesctl agent run backend "Implementiere die begrenzte Aufgabe"
```

`agent create` erzeugt standardmäßig ein gleichnamiges, isoliertes Credential.
Explizite Broker-Verwaltung ist ebenfalls möglich:

```bash
./hermesctl credential create firma-codex --engine codex
./hermesctl agent create backend --engine codex --credential firma-codex
./hermesctl credential status firma-codex
./hermesctl credential list
```

Ein Credential darf standardmäßig nur einer Instanz zugeordnet sein. Teilen
erfordert `--share-credential` beziehungsweise `--share`; dadurch ist das
Risiko paralleler Token-Aktualisierungen ausdrücklich sichtbar. Der Broker
liest keine Secret-Datei und gibt weder Token noch Home-Pfad über MCP oder
Skills zurück. `credential delete` verlangt `--yes`, verweigert zugewiesene
Credentials und entfernt den CLI-Home für dieses Projekt. Eine Instanz wird mit
`agent delete <id> --yes` entfernt; ihr Broker-Home bleibt standardmäßig
erhalten. Nur `--delete-credential` entfernt ihn im selben Schritt ebenfalls.
Das ist keine kryptografisch sichere Löschung; Dateisystem-/Backup-Recovery
kann nicht ausgeschlossen werden.

Die generischen Rechte sind feiner als die Legacy-Worker-Spec:

| Recht | Wirkung |
|---|---|
| `inspect` | Read/Search-Werkzeuge |
| `edit` | eingebaute Edit-/Write-Werkzeuge; benötigt `inspect` |
| `commandline` | Bash/Shell; benötigt `inspect+network` |
| `network` | explizite Bestätigung, dass Shell und Provider den Container-Egress teilen |
| `skills` | nur geprüfte SKILL.md-Inhalte aus dem Instanzkontext |
| `agents-md` / `claude-md` | jeweils genau die geschützte Instanzdatei |

Bei Codex sind `inspect+edit+commandline+network` ein `[special]`-Bündel. Bei
Claude und OpenCode bleiben Inspect, Edit und Bash tatsächlich getrennt.

Teams werden deklarativ und wiederholbar angewendet. Eine Vorlage liefert:

```bash
./hermesctl team example > team.yaml
./hermesctl team apply team.yaml --explain   # nur Plan, keine Änderung
./hermesctl team apply team.yaml
./hermesctl team status software-team
./hermesctl team run software-team
./hermesctl team reset software-team
```

`team.yaml` Version 1 beschreibt Agenten, Engines, Rollen, Credentials,
Modelle, exakte Rechte und einen azyklischen Workflow über `needs`. Schritte
können `approval: true`, `input_artifacts` und ein Timeout setzen. Ausgaben
werden als unveränderliche, SHA-256-geprüfte Artefakte gespeichert und den
abhängigen Schritten als Handoff übergeben. Es gibt keinen implizit gemeinsam
beschreibbaren Team-Workspace. Für Git-Arbeit erhält jeder Agent einen eigenen
Clone; Patches werden kontrolliert exportiert:

```bash
./hermesctl agent patch backend backend-fix
./hermesctl artifact show backend-fix
./hermesctl artifact get backend-fix ./backend-fix.patch
```

Einzelne persistente Jobs sind unabhängig vom Team-DAG verfügbar:

```bash
./hermesctl job submit backend "Führe die Tests aus" --wait
./hermesctl job status job-0123456789abcdef
./hermesctl job cancel job-0123456789abcdef
./hermesctl artifact list
```

Hermes erhält generischen Zugriff nur nach einer eigenen Freigabe. MCP ist der
engere Weg:

```bash
./hermesctl enable orchestrator skills agents-mcp
./hermesctl chat
```

`agents-mcp` registriert exakt `list`, `status`, `run`, `job_submit`,
`job_status`, `job_cancel`, `artifact_get`, `team_list` und `team_status` unter
`mcp__agents__*`. Es kann weder Container starten noch Rechte, Teams,
Credentials oder Login verändern. Agent-Container müssen der Operator vorher
starten. Der alternative direkte Weg benötigt die netzlose Hermes-
Command-Sandbox:

```bash
./hermesctl enable orchestrator skills commandline agents-direct
```

Der Skill darf dort ausschließlich `registered-agent list|status|run` nutzen.
Credential- und private Control-Verzeichnisse werden nicht gemountet; die
read-only Registry enthält nur `credential_id: redacted`.

Soll Hermes zusätzlich die granularen Agent-Rechte verwalten, wird unabhängig
`hermesctl-mcp` oder `hermesctl-direct` freigegeben. Diese Metaebene bietet nur
`agent_list`, `agent_rights`, `agent_explain`, `agent_grant`, `agent_revoke`
und `agent_reset`; Broker-Zuordnung, Login, Docker, Teamdefinitionen und
Job-/Artefaktadministration bleiben Operator-Sache.

## Generische Evaluationen und Benchmarks

Die Evaluation Engine vergleicht registrierte Agenten, Modelle, Rechteprofile,
Kontexte und Skills ohne feste Aufgabe oder fest einprogrammierte Worker. Das
Projekt enthält bewusst kein fachliches Kursbeispiel oder Ground Truth. Eine
neutrale Manifeststruktur wird ausgegeben mit:

```bash
./hermesctl benchmark example > evaluation.yaml
```

Ein Manifest Version 1 beschreibt Agenten und Abrechnungsmodus, Prompt und
optionalen Basis-Workspace, Varianten mit exakten Rechten und temporären
Workspace-/Kontext-Overlays, Wiederholungen, Warm-ups, Reihenfolge, Seed,
Timeout, Evaluator und Metriken. Eingebaut sind `contains`, `exact-json` und
`regex-fields`; weitere Evaluatoren werden über die Registry ergänzt.

```bash
./hermesctl benchmark plan evaluation.yaml
./hermesctl benchmark create evaluation.yaml
./hermesctl benchmark run eval-0123456789abcdef
./hermesctl benchmark status eval-0123456789abcdef
./hermesctl benchmark cancel eval-0123456789abcdef
./hermesctl benchmark resume eval-0123456789abcdef
./hermesctl benchmark export eval-0123456789abcdef result.json
```

`plan` verändert nichts. `create` speichert Prompt, Workspace, Overlays und
Kontexte als unveränderliche SHA-256-Artefakte. Jeder Trial verwendet einen
kurzlebigen Agenten mit eigenem State, Workspace, Kontext, Socket und
Container. Nur der zugewiesene Broker-CLI-Home des Quellagenten wird geteilt;
Manifest v1 erzwingt deshalb genau einen laufenden Trial je Credential. Der
Quellagent und `worker-context/*` werden nie verändert.

Läufe sind persistent, abbrechbar und wiederaufnehmbar. Ein Abbruch wird an den
aktiven Job und CLI-Prozess weitergereicht. Scheitert das Aufräumen, werden die
Trial-Rechte auf null gesetzt und der Diagnosezustand bleibt erhalten.

Ergebnisse enthalten Engine, Modell, gepinnte CLI-Version, Rechte-/Enforcement-
Snapshot, Artefaktprüfsummen, Git-Commit, Seed, Dauer, Timeout-/Truncation-
Status und getrennte Input-, Output-, Reasoning-, Cache-Read- und
Cache-Write-Tokens. Zusammenfassungen liefern Passrate mit Wilson-95%-Intervall,
Median, p95 sowie Token-/Kostenaggregate. Bei Monatsabos wird kein
hypothetischer API-Preis ausgewiesen; `reported_cost_usd` ist nur mit
`billing_mode: api` möglich.

Die lokale Operatoroberfläche startet mit:

```bash
./hermesctl benchmark serve
```

Sie bindet an `127.0.0.1`, verwendet einen zufälligen Bearer-Token im
kurzlebigen URL-Fragment, prüft Browser-Origin und setzt CSP/No-Store.
Benchmark-Administration wird weder als Skill noch als MCP-Werkzeug an Hermes
gegeben.

Die tokenpflichtige API umfasst ausschließlich:

```text
GET  /api/v1/health
GET  /api/v1/meta
GET  /api/v1/experiments
GET  /api/v1/experiments/{id}
GET  /api/v1/experiments/{id}/results/{trial-id}
POST /api/v1/experiments                    {"manifest_path":"..."}
POST /api/v1/experiments/{id}/run
POST /api/v1/experiments/{id}/resume
POST /api/v1/experiments/{id}/cancel
```

## Drei unabhängige Coding-Worker

Hermes kann Codex CLI, Claude Code und OpenCode orchestrieren, ohne eine der
drei CLIs in seinen eigenen Container zu installieren. Jeder Worker besitzt:

- einen eigenen gehärteten Docker-Container und ein eigenes, nicht auf
  Provider-Domains gefiltertes Egress-Netz,
- einen eigenen Login- und Konfigurations-State,
- einen eigenen Workspace,
- einen eigenen Unix-Socket ohne TCP-Port,
- einen eigenen Skill sowie getrennte Direct- und MCP-Schalter,
- ein eigenes, zunächst leeres Worker-Rechteprofil,
- read-only eingebundene, operatorgeprüfte Kontext- und Skill-Dateien.

Die Verzeichnisse liegen unter `runtime/workers/<worker>/state`,
`runtime/workers/<worker>/workspace` und `runtime/workers/<worker>/socket`.
Kein Worker sieht den Hermes-Workspace oder den State eines anderen Workers.
Die Rechteprofile liegen davon getrennt unter
`runtime/control/workers/<worker>/capabilities`. Der Worker sieht nur sein
eigenes Profil read-only und kann sich daher auch mit Shell-Recht nicht selbst
hochstufen.

### Installation, Login und Modell

Diese Schritte werden immer direkt vom Benutzer als Operator ausgeführt. Sie
sind absichtlich weder Direct- noch MCP-Tools für Hermes:

```bash
# Gepinnte Images bauen
./hermesctl worker codex build
./hermesctl worker claude build
./hermesctl worker opencode build

# Jeweils eigener interaktiver Account-/Provider-Login
./hermesctl worker codex login
./hermesctl worker claude login
./hermesctl worker opencode login

# Status und optionale, unabhängige Modellwahl
./hermesctl worker codex status
./hermesctl worker codex model <model-id>
./hermesctl worker codex model none
```

Alternativ stellt `hermesctl` für die beiden vendor-eigenen Abo-Flows eine
lokale API mit HTML-/JavaScript-Oberfläche bereit:

```bash
# Worker-Images müssen vorher gebaut sein
./hermesctl worker codex build
./hermesctl worker claude build

# Bindet hart an 127.0.0.1:8765 und öffnet die lokale UI
./hermesctl login-ui
```

Die Startausgabe enthält eine URL mit einem zufälligen Token im URL-Fragment.
JavaScript übernimmt das Token nur in den `Authorization: Bearer`-Header und
entfernt das Fragment sofort aus der Adresszeile. Die statische Seite enthält
keine Credentials; sämtliche API-Endpunkte benötigen das Token.

Die Oberfläche bietet für die beiden festen Worker und für registrierte
Codex-/Claude-Agenten exakt dieselben festen Abo-Abläufe. Bei Agenten mountet
der Broker nur den zugewiesenen CLI-Home:

- **Codex CLI:** `codex login --device-auth` für den ChatGPT-Abozugang. Link
  öffnen und den Einmalcode auf der OpenAI-Seite eingeben.
- **Claude Code:** `claude auth login --claudeai` für Claude Pro, Max, Team
  oder Enterprise. Kann der Browser den Container-Callback nicht erreichen,
  wird der angezeigte Rückgabecode über das Login-Terminal der UI eingefügt.

Das Webterminal zeigt nur die Ausgabe dieses festen Login-Prozesses, nimmt
höchstens 4096 Zeichen pro Eingabe an und erlaubt Abbruch. Es akzeptiert weder
frei wählbare CLI-Argumente noch API-Key-/Console-Modi. OpenCode bleibt wegen
seiner eigenen Provider- und Plugin-Flows beim Operator-Kommando
`./hermesctl worker opencode login`.

Für einen API-Client kann der Browserstart unterdrückt werden:

```bash
./hermesctl login-ui --no-browser --port 8765
```

Nach dem Kopieren des ausgegebenen Bearer-Tokens sind verfügbar:

```text
GET    /api/v1/health
GET    /api/v1/workers/{codex|claude}/status
GET    /api/v1/agents
GET    /api/v1/agents/{agent-id}/status
GET    /api/v1/control-summary
POST   /api/v1/login-sessions
GET    /api/v1/login-sessions/{id}?offset=0
POST   /api/v1/login-sessions/{id}/input
DELETE /api/v1/login-sessions/{id}
```

Beispiel mit dem beim Start ausgegebenen Token:

```bash
LOGIN_UI_TOKEN='<aus-der-Startausgabe>'
curl -H "Authorization: Bearer $LOGIN_UI_TOKEN" \
  http://127.0.0.1:8765/api/v1/health
curl -H "Authorization: Bearer $LOGIN_UI_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"worker":"codex"}' \
  http://127.0.0.1:8765/api/v1/login-sessions
```

`POST /api/v1/login-sessions` akzeptiert ausschließlich
`{"worker":"codex"}`, `{"worker":"claude"}` oder
`{"agent":"<registrierte-id>"}`. Für Agenten liest die API nur Engine und
Rolle aus der Registry; Credential-Inhalte bleiben außerhalb des Prozesses.
Das read-only Dashboard zeigt Agenten, Teams und Jobzustände ohne Prompts oder
Secrets. Die API bindet nicht an
LAN-Adressen, setzt No-Store-/CSP-/Frame-Schutzheader und verwirft fremde
Browser-Origins. `Ctrl-C` beendet den Server und bricht laufende Login-Prozesse
ab. Diese Oberfläche ist eine reine Operator-Funktion: Sie wird weder als
Hermes-Skill noch als MCP-Tool registriert.

`none` verwendet wieder den jeweiligen CLI-Default. Die gleichen
`status`-/`model`-Befehle gelten mit `claude` und `opencode`. Ein Worker wird
bei Verwendung automatisch gestartet; manuell geht es mit
`./hermesctl worker <worker> start|stop`. Ein Operator-Testauftrag ist mit
`./hermesctl worker <worker> run "Aufgabe"` möglich.

Codex CLI kann sich direkt mit einem ChatGPT-Konto anmelden; der voreingestellte
Device-Login eignet sich auch für einen Container. Claude Code kann direkt ein
Claude-Pro-, Max-, Team- oder Enterprise-Konto verwenden. OpenCode verwaltet
seine Provider selbst und unterstützt unter anderem ChatGPT Plus/Pro. Ein
Claude-Pro-/Max-Abo
wird in OpenCode bewusst **nicht** verwendet: OpenCode hat die entsprechenden
Drittanbieter-Plugins wegen Anthropic-Vorgaben ab Version 1.3.0 entfernt. Für
ein Claude-Monatsabo ist deshalb ausschließlich der getrennte Claude-Code-
Worker vorgesehen.

Offizielle Referenzen:

- [Codex: Anmeldung mit ChatGPT oder API-Key](https://developers.openai.com/codex/auth/)
- [Claude Code: Authentifizierung](https://code.claude.com/docs/en/authentication)
- [Claude Code: CLI](https://code.claude.com/docs/en/cli-usage)
- [OpenCode: Provider und Account-Grenzen](https://opencode.ai/docs/providers/)
- [OpenCode: CLI](https://opencode.ai/docs/cli/)

### Worker-Fähigkeiten einzeln schalten

Ein angemeldeter und erreichbarer Worker beginnt weiterhin **model-only**. Er
kann antworten, besitzt aber weder Datei-/Shell-Tools noch Skills oder
Projektkontext. Dieses separate Profil ist die Worker-Spec; jeder Schalter
gilt nur für genau einen Worker:

```bash
# Erst ansehen
./hermesctl access codex status
./hermesctl access codex explain

# Legacy-Toolstufe; bei Codex bleibt die Shell wegen innerem bwrap-Fehler aus
./hermesctl access codex enable tool-use

# Commandline separat; sie benötigt tool-use
./hermesctl access codex enable commandline

# Drei voneinander unabhängige Kontextquellen
./hermesctl access codex enable skills
./hermesctl access codex enable agents-md
./hermesctl access codex enable claude-md

# Einzeln entziehen oder nur diesen Worker vollständig zurücksetzen
./hermesctl access codex disable claude-md
./hermesctl access codex reset
```

Die gleichen Befehle gelten für `claude` und `opencode`. Änderungen greifen
beim **nächsten Auftrag dieses Workers**; weder ein Container-Neustart noch
eine neue Hermes-Sitzung ist dafür nötig. Ein schon laufender Worker-Auftrag
behält seinen beim Start gelesenen Snapshot.

Die geschützten Quellen sind direkt und übersichtlich editierbar:

```text
worker-context/<worker>/AGENTS.md
worker-context/<worker>/CLAUDE.md
worker-context/<worker>/skills/<skill>/SKILL.md
```

Sie werden read-only in genau den passenden Worker gemountet. `skills`
injiziert die genehmigten `SKILL.md`-Anweisungen, während die nativen,
dynamischen Skill-/Plugin-Oberflächen gesperrt bleiben. So bleibt ein Skill
auch dann gezielt kontrollierbar, wenn die drei CLIs ihre nativen Skill-Systeme
unterschiedlich behandeln. Automatisch erkennbare Kontext- oder Skill-Dateien
im privaten Workspace führen bei ausgeschaltetem zugehörigem Feature zu einem
fail-closed Abbruch statt zu einem stillen Policy-Bypass.

Die technische Abbildung ist bewusst nicht künstlich gleichgezogen:

| Worker | `tools` | `commandline` | Kontext-Sperre |
|---|---|---|---|
| Codex CLI | kein separates File-Tool; Shell bleibt ohne `commandline` fail-closed aus | aktiviert Shell mit äußerer Docker-Isolation, weil inneres bubblewrap nicht initialisieren kann | automatische Regeln aus, freigegebene Dateien als Developer-Instructions |
| Claude Code | exakt `Read, Glob, Grep, Edit, Write` | fügt ausschließlich `Bash` hinzu | Safe Mode, leere MCP-Konfiguration, freigegebene Dateien explizit angehängt |
| OpenCode | nur `read, glob, grep, edit` | erlaubt zusätzlich `bash` | Project-Config/externe Skills aus, alle übrigen Permissions explizit `deny` |

Bei Codex bedeutet `tools` allein daher kein ausführbares Tool. Erst
`tools+commandline` aktiviert die gemeinsame Shell mit Workspace-Schreibrecht;
eine echte Trennung in native Read-/Edit- und Shell-Tools bietet dieser Adapter
nicht. Bei Claude und OpenCode sind
Dateiwerkzeuge und Bash tatsächlich getrennte Tooloberflächen.

Offizielle Details zu diesen Unterschieden:

- [Codex: Konfiguration, Sandbox und Features](https://developers.openai.com/codex/config-reference/)
- [Codex: AGENTS.md und Skills](https://developers.openai.com/codex/concepts/customization)
- [Claude Code: CLI-Schalter](https://code.claude.com/docs/en/cli-reference)
- [Claude Code: CLAUDE.md und AGENTS.md-Import](https://code.claude.com/docs/en/memory)
- [Claude Code: Skills](https://code.claude.com/docs/en/skills)
- [OpenCode: Permissions](https://opencode.ai/docs/permissions/)
- [OpenCode: AGENTS.md/CLAUDE.md-Regeln](https://opencode.ai/docs/rules/)
- [OpenCode: Skills](https://opencode.ai/docs/skills/)

### Hermes-Zugriff einzeln freigeben

MCP ist der engere Weg und benötigt keine allgemeine Commandline-Capability:

```bash
./hermesctl enable orchestrator skills codex-mcp
./hermesctl chat
```

Direkter Skill-Befehl läuft in der weiterhin netzlosen Command-Sandbox. Diese
Sandbox erhält nur den read-only eingebundenen Socket des gewählten Workers:

```bash
./hermesctl enable orchestrator skills commandline codex-direct
./hermesctl chat
```

Beide Wege können wahlweise gleichzeitig freigegeben werden; der Skill
bevorzugt dann MCP und führt einen Auftrag nicht doppelt aus:

```bash
./hermesctl enable orchestrator skills commandline codex-direct codex-mcp
```

`codex` lässt sich in allen Beispielen durch `claude` oder `opencode` ersetzen.
Die Capabilities sind vollständig unabhängig, daher sind auch Kombinationen
wie `codex-mcp claude-direct` möglich. Pro MCP-Worker werden exakt zwei Tools
registriert, zum Beispiel:

- `mcp__codex_worker__status`
- `mcp__codex_worker__run`

Der direkte Skill verwendet entsprechend nur `agent-worker codex status` und
`agent-worker codex run "..."`. `run` allein öffnet nur den modell-only Worker;
seine tatsächlichen Aktionsrechte kommen aus dem separaten Worker-Profil.
Insbesondere `tools` und `commandline` sind starke Berechtigungen. Der Worker
benötigt außerdem unabhängig von `shell-network` Egress zu seinem
Modellprovider; die Docker-Bridge begrenzt dieses Netz technisch nicht auf
Provider-Domains.

Soll Hermes die Worker-Profile selbst orchestrieren, braucht es zusätzlich
`hermesctl-mcp` oder `hermesctl-direct`. Beispiel über den engeren MCP-Weg:

```bash
./hermesctl enable orchestrator skills hermesctl-mcp codex-mcp
./hermesctl chat
```

Hermes prüft dann zunächst `mcp__hermesctl__access_status` mit `target="codex"`
und bei Bedarf `mcp__hermesctl__access_explain`, erklärt Wirkung
und Risiko und darf erst nach ausdrücklicher Benutzerfreigabe beispielsweise
`mcp__hermesctl__access_enable` mit `target="codex"` und
`features=["tool-use"]` ausführen. Login, Modellwahl und Containerverwaltung
bleiben auch dabei reine Operator-Aktionen.

## Was technisch erzwungen wird

- Der Launcher registriert für den Nullzustand ein gültiges, leeres Hermes-
  Toolset. Dadurch fällt Hermes nicht auf `hermes-cli` und dessen Vollausstattung
  zurück.
- Jeder Neuaufbau der Toolschemas wird auf die freigegebenen Toolsets gepinnt.
  Auch `/tools` oder ein später Refresh kann die Policy nicht erweitern.
- `HERMES_IGNORE_RULES=1` unterbindet Kontextdateien und persistentes Memory.
  Nur `orchestrator`/`agents-md` beziehungsweise `claude-md` heben diese Sperre
  für ihre exakten, read-only unter `/policy-context` eingebundenen Quellen
  auf. Die Hülle injiziert nur die ausgewählten Dateien und ignoriert
  gleichnamige Workspace-Dateien. Memory bleibt laut Konfiguration aus; eine
  leere `SOUL.md` verhindert zusätzlich das Seeding einer eigenen Persona.
- `.no-bundled-skills` verhindert, dass der offizielle Docker-Start die
  mitgelieferten Skills synchronisiert.
- `HERMES_SAFE_MODE=1` deaktiviert Plugin-Discovery, Shell-Hooks und MCP. Nur
  eine explizite `*-mcp`-Capability öffnet MCP wieder; Plugins und Hooks bleiben
  blockiert und die MCP-Konfiguration wird auf die freigegebenen lokalen
  Server fest verdrahtet.
- Datei-, Shell- und Codezugriffe laufen über Hermes' Docker-Backend in einem
  zweiten Container. Dieser erhält nur `runtime/workspace`, keine Provider-
  Secrets, keine Cache-Verzeichnisse und ohne `skills` auch kein Skill-
  Verzeichnis.
- Der Commandline-Container hat standardmäßig kein Netzwerk. Egress kommt erst
  mit `shell-network` hinzu.
- Der Docker-Socket wird nur in den Hermes-Controller gemountet, wenn eine
  Sandbox-Fähigkeit tatsächlich aktiv ist. Er wird nie in den model-facing
  Commandline-Container weitergereicht.
- Jeder Worker liest vor jedem Auftrag sein separates Profil aus einem
  read-only Control-Mount. Die CLI wird daraus mit einer exakten Tool-/Permission-
  Konfiguration gestartet; unfreigegebene automatische Workspace-Kontexte
  lassen den Auftrag fail-closed abbrechen.
- Jede generische Instanz erhält eigene State-, Workspace-, Kontext-, Socket-
  und Control-Pfade. Der Credential-Broker mountet nur ihren zugewiesenen
  CLI-Home; die model-facing Registry ist davon getrennt und redigiert.
- Teams teilen Ergebnisse ausschließlich als größenbegrenzte,
  prüfsummengeschützte Artefakte. Jobzustände werden atomar persistiert;
  Abbruch läuft über eine enge `cancel`-RPC zum betroffenen Agenten.

Die effektive Policy lässt sich ohne Modell/API-Aufruf prüfen:

```bash
./hermesctl verify
```

## Gegenüberstellung: dieses Setup vs. vorgesehenes Hermes

| Bereich | Hermes regulär | Hermes Naked |
|---|---|---|
| Ziel | Persönlicher Agent, der über Sitzungen wächst | Kontrollierte Freigabe nach Least Privilege |
| Start-Tools | `hermes-cli` umfasst standardmäßig u. a. Files, Terminal, Web, Skills, Memory und Delegation | Kein einziges model-facing Tool |
| Skills | Bundled Skills werden synchronisiert; Agent kann Skills nutzen/entwickeln | Kein Seeding; Tool und Mount separat abschaltbar |
| Kontext | Projektdateien und `SOUL.md` werden automatisch in den Systemprompt geladen | Standardmäßig ignoriert; geschützte `AGENTS.md` und `CLAUDE.md` sind separat opt-in |
| Memory | Langfristiges Lernen/Profil ist Kernfunktion | Deaktiviert und nicht geladen |
| Shell | Standardmäßig lokales Backend möglich | Zweiter Container, kein Host-Mount außer Workspace, zunächst air-gapped |
| Erweiterungen | Plugins, MCP und Hooks erweitern den Agenten dynamisch | Standardmäßig blockiert; optional nur lokal fest verdrahtete Hermesctl-/Worker-/Agent-MCPs |
| Coding-Agenten | Delegation kann Teil des allgemeinen Agenten-Ökosystems sein | Drei kompatible Worker plus beliebig viele registrierte, vollständig getrennte Instanzen |
| Teams | Allgemeine Agentendelegation | Deklarative DAGs, exakte Rollen/Rechte, persistente Jobs und immutable Artefakt-Handoffs |
| Credentials | CLI-/Provider-spezifischer State | Broker-Zuordnung pro Instanz; keine Secrets über Skills/MCP/Dashboard |
| Bedienung | Maximale Autonomie und Komfort | Gemeinsame `access`-Schalter, aber weiterhin explizite und auditierbare Freigaben |

Der Vorteil von Hermes Naked ist die kleine und nachvollziehbare Angriffsfläche:
unbekannte Projekte, Inhalte oder Modelle bekommen zunächst keinerlei
Aktionsrecht. Man kann Wirkung und Risiko jeder neuen Fähigkeit einzeln testen.

Der Vorteil des regulären Hermes-Modells ist genau die andere Seite: Skills,
Memory, Tools, Automationen und Delegation bilden einen lernenden persönlichen
Agenten mit deutlich weniger Reibung. Für vertrauenswürdige, persönliche
Workflows ist das leistungsfähiger; für eine stufenweise Sicherheitsfreigabe
ist der reguläre Default zu breit.

Details zu Grenzen und Restrisiken stehen in
[`docs/SECURITY.md`](docs/SECURITY.md).

Offizielle Referenzen:

- [Hermes Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker)
- [Tools und Toolsets](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools)
- [Security-Modell](https://hermes-agent.nousresearch.com/docs/user-guide/security)
- [CLI-Optionen (`--ignore-rules`, `--safe-mode`)](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)
