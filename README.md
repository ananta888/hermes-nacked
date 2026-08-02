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

## Rechte einzeln schalten

```bash
# Datei-Tools: nur runtime/workspace, lesen und schreiben
./hermesctl enable files

# Commandline: separater, gehärteter und zunächst netzloser Container
./hermesctl enable commandline

# Skills: Liste/Ansicht/Verwaltung; weiterhin keine Bundled Skills
./hermesctl enable skills

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
`hermesctl-mcp`, `codex-worker`, `claude-worker` und `opencode-worker` werden
in den persistenten Hermes-State synchronisiert.
Installiert bedeutet nicht freigegeben: Ohne `skills` erscheinen sie weder im
Skill-Index noch als Skill-Tools.

Alle Schalter zeigt `./hermesctl capabilities`. `tools` ist ein Alias für
`files`, `shell` und `terminal` sind Aliase für `commandline`.

Skills können durch den Operator installiert werden, ohne sie dem Agenten
sofort freizugeben:

```bash
./hermesctl skills list
./hermesctl skills install <quelle>
./hermesctl enable skills
```

## Hermes als eigener Orchestrator

Die Orchestrator-Anweisungen in `AGENTS.md`, die Skills und die zwei
Steuerungswege sind getrennte Berechtigungen. Der Nullzustand lädt keine davon.

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

`orchestrator` lädt nur die projektgebundene `AGENTS.md` und gibt selbst kein
Tool frei. `hermesctl-direct` benötigt `skills` und `commandline`.
`hermesctl-mcp` benötigt `skills` und registriert ausschließlich:

- `mcp__hermesctl__status`
- `mcp__hermesctl__list_capabilities`
- `mcp__hermesctl__enable`
- `mcp__hermesctl__disable`
- `mcp__hermesctl__reset`

Beide `hermesctl-*`-Capabilities sind Meta-Berechtigungen: Ein damit
ausgestattetes Modell kann die Policy für die nächste Sitzung verändern. Eine
laufende Sitzung erhält dadurch keine neuen Tools; nach einer Änderung muss sie
beendet und neu gestartet werden. `reset` entfernt auch diese Meta-Rechte und
den Orchestrator-Kontext.

## Drei unabhängige Coding-Worker

Hermes kann Codex CLI, Claude Code und OpenCode orchestrieren, ohne eine der
drei CLIs in seinen eigenen Container zu installieren. Jeder Worker besitzt:

- einen eigenen gehärteten Docker-Container und ein eigenes, nicht auf
  Provider-Domains gefiltertes Egress-Netz,
- einen eigenen Login- und Konfigurations-State,
- einen eigenen Workspace,
- einen eigenen Unix-Socket ohne TCP-Port,
- einen eigenen Skill sowie getrennte Direct- und MCP-Schalter.

Die Verzeichnisse liegen unter `runtime/workers/<worker>/state`,
`runtime/workers/<worker>/workspace` und `runtime/workers/<worker>/socket`.
Kein Worker sieht den Hermes-Workspace oder den State eines anderen Workers.

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

`none` verwendet wieder den jeweiligen CLI-Default. Die gleichen
`status`-/`model`-Befehle gelten mit `claude` und `opencode`. Ein Worker wird
bei Verwendung automatisch gestartet; manuell geht es mit
`./hermesctl worker <worker> start|stop`. Ein Operator-Testauftrag ist mit
`./hermesctl worker <worker> run "Aufgabe"` möglich.

Codex CLI kann sich direkt mit einem ChatGPT-Konto anmelden; der voreingestellte
Device-Login eignet sich auch für einen Container. Claude Code kann direkt das
Claude-Pro- oder Claude-Max-Konto verwenden. OpenCode verwaltet seine Provider
selbst und unterstützt unter anderem ChatGPT Plus/Pro. Ein Claude-Pro-/Max-Abo
wird in OpenCode bewusst **nicht** verwendet: OpenCode hat die entsprechenden
Drittanbieter-Plugins wegen Anthropic-Vorgaben ab Version 1.3.0 entfernt. Für
ein Claude-Monatsabo ist deshalb ausschließlich der getrennte Claude-Code-
Worker vorgesehen.

Offizielle Referenzen:

- [Codex CLI: Anmeldung mit ChatGPT](https://developers.openai.com/codex/cli/)
- [Claude Code: Authentifizierung](https://code.claude.com/docs/en/authentication)
- [Claude Code: CLI](https://code.claude.com/docs/en/cli-usage)
- [OpenCode: Provider und Account-Grenzen](https://opencode.ai/docs/providers/)
- [OpenCode: CLI](https://opencode.ai/docs/cli/)

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
`agent-worker codex run "..."`. `run` ist trotz der kleinen RPC-Oberfläche
eine starke Berechtigung: Die dahinterliegende Coding-CLI darf im privaten
Worker-Workspace Dateien ändern und Befehle ausführen. Der Worker benötigt
außerdem unabhängig von `shell-network` Egress zu seinem Modellprovider; die
Docker-Bridge begrenzt dieses Netz technisch nicht auf Provider-Domains.

## Was technisch erzwungen wird

- Der Launcher registriert für den Nullzustand ein gültiges, leeres Hermes-
  Toolset. Dadurch fällt Hermes nicht auf `hermes-cli` und dessen Vollausstattung
  zurück.
- Jeder Neuaufbau der Toolschemas wird auf die freigegebenen Toolsets gepinnt.
  Auch `/tools` oder ein später Refresh kann die Policy nicht erweitern.
- `HERMES_IGNORE_RULES=1` unterbindet Kontextdateien und persistentes Memory.
  Nur `orchestrator` hebt diese Sperre für die read-only eingebundene
  Projekt-`AGENTS.md` auf. Memory bleibt laut Konfiguration aus; eine leere
  `SOUL.md` verhindert zusätzlich das Seeding einer eigenen Persona.
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
| Kontext | Projektdateien und `SOUL.md` werden automatisch in den Systemprompt geladen | Standardmäßig ignoriert; nur die Orchestrator-`AGENTS.md` ist separat opt-in |
| Memory | Langfristiges Lernen/Profil ist Kernfunktion | Deaktiviert und nicht geladen |
| Shell | Standardmäßig lokales Backend möglich | Zweiter Container, kein Host-Mount außer Workspace, zunächst air-gapped |
| Erweiterungen | Plugins, MCP und Hooks erweitern den Agenten dynamisch | Standardmäßig blockiert; optional nur lokal fest verdrahtete Hermesctl-/Worker-MCPs |
| Coding-Agenten | Delegation kann Teil des allgemeinen Agenten-Ökosystems sein | Drei separat gepinnte Container, States, Workspaces, Skills, Sockets und Rechte |
| Bedienung | Maximale Autonomie und Komfort | Explizite, auditierbare Schalter mit mehr Bedienaufwand |

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
