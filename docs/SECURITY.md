# Sicherheitsmodell und Grenzen

## Vertrauensgrenzen

```text
Benutzer
  ├─ lokale Login-UI/API (127.0.0.1, zufälliger Bearer-Token)
  │    ├─ festes Codex- oder Claude-CLI-Login im jeweiligen Worker-State
  │    └─ festes Login einer registrierten Instanz in genau deren Broker-Home
  └─ Hermes-Controller (Docker, Providerzugang, kein Host-Projekt)
       ├─ optional: read-only AGENTS.md und/oder CLAUDE.md
       ├─ optional: lokaler hermesctl-MCP (nur Hermes-/Worker-Policy)
       ├─ optional: eng begrenzte Worker-MCPs (je nur status/run)
       ├─ optional: generischer Agent-MCP (Agenten/Jobs/Artefakte/Teams)
       ├─ optional: read-only Unix-Socket des freigegebenen Workers
       ├─ Modell-API (nur für Inferenz)
       └─ optional: Docker-Socket
            └─ Command-Sandbox (nur Workspace, standardmäßig ohne Netzwerk)

  ├─ Codex-Worker (eigener State, Workspace, Socket und Egress)
  ├─ Claude-Worker (eigener State, Workspace, Socket und Egress)
  └─ OpenCode-Worker (eigener State, Workspace, Socket und Egress)

  ├─ registrierter Agent A (eigener State, Workspace, Kontext, Socket, Container)
  │    └─ Credential-Broker mountet nur CLI-Home A
  └─ registrierter Agent B (vollständig getrennte Instanz)
       └─ Credential-Broker mountet nur CLI-Home B

Operator-/Hermes-Control
  └─ runtime/control/workers/<worker>/capabilities (read/write)
       └─ jeweiliger Worker (nur eigenes Profil, read-only)

Operator-Control-Plane
  ├─ runtime/control/agents/<id> (privat, enthält Broker-Zuordnung)
  ├─ runtime/registry/agents/<id> (read-only, credential_id=redacted)
  ├─ runtime/credentials/<credential>/home (nur zugewiesener Agent)
  ├─ runtime/jobs und runtime/artifacts (atomare Team-Handoffs)
  ├─ runtime/evaluations (persistente Pläne, Trials und Messwerte)
  └─ runtime/teams (angewendete deklarative Definitionen)
```

Im Nullzustand wird der Docker-Socket nicht gemountet. Er kommt erst hinzu,
wenn `files`, `commandline` oder `code` aktiv ist. Der Agent erhält den Socket
nie als Datei oder Environmentvariable; Hermes benutzt ihn als Controller, um
den zweiten Sandbox-Container zu verwalten.

## Garantien dieses Projekts

- Unbekannte Capability-Namen brechen den Start ab (fail closed).
- Die Abo-Login-API bindet fest an IPv4-Loopback, erzeugt bei jedem Start einen
  zufälligen Bearer-Token und akzeptiert ausschließlich feste Codex-Device-
  beziehungsweise Claude.ai-Login-Kommandos. Freie Argumente, API-Key-Modi,
  Login-Dateizugriff und OpenCode-Providerwahl sind nicht Teil der API.
- Login-Sitzungen laufen in einem PTY mit begrenztem Ausgabepuffer und
  begrenzter Eingabe. Je Worker darf nur eine Sitzung laufen; Serverende
  signalisiert allen laufenden Login-Prozessgruppen den Abbruch.
- Registrierte Agenten werden in der Login-API nur mit ID, Engine und Rolle
  angezeigt. Der feste Login-Prozess wird über `hermesctl agent login <id>`
  gestartet; die UI liest weder Broker-Metadaten mit Credential-ID noch
  Secret-Dateien.
- Die gemeinsame `access`-Oberfläche akzeptiert nur die vier Ziele `hermes`,
  `codex`, `claude`, `opencode` und die fünf Features `tool-use`,
  `commandline`, `skills`, `agents-md`, `claude-md`. Sie schreibt weiterhin
  nur das exakte Hermes- oder Einzel-Worker-Profil; ein Target-Reset berührt
  kein anderes Ziel.
- Ohne Capability ist die effektive Toolliste leer.
- Provider-Credentials werden nicht an die Command-Sandbox weitergereicht.
- Nur `runtime/workspace` wird schreibbar in die Command-Sandbox gemountet.
- Skill-, Credential- und Mediencache-Mounts werden standardmäßig unterdrückt.
- Skills werden nur gemountet, wenn `skills` aktiv ist; Skill-Credentials auch
  dann nicht automatisch.
- Kontextregeln werden standardmäßig nicht geladen. `orchestrator`/`agents-md`
  und `claude-md` binden ihre exakten projektgebundenen Dateien getrennt unter
  `/policy-context` read-only ein. Die Policy injiziert nur die ausgewählten
  Quellen; persistentes Memory bleibt deaktiviert.
- Plugin- und Hook-Discovery bleibt aus. MCP bleibt ebenfalls aus, außer eine
  explizite `*-mcp`-Capability ist aktiv; dann ist die Discovery auf die
  freigegebenen lokalen Server und deren exakte Toolnamen begrenzt.
- Der direkte `hermesctl`-Zugriff mountet nur den Control-Client, die beiden
  Policy-Module, die Hermes-Capability-Datei und die Worker-Profile in die
  netzlose Command-Sandbox. Docker-Administration und die Projektwurzel werden
  nicht sichtbar.
  Für registrierte Agenten wird nur die redigierte Registry doppelt als
  Policy-Repository gemountet; private Broker-Zuordnungen und Credential-Homes
  bleiben unsichtbar. Zulässig sind nur list/rights/explain/grant/revoke/reset.
- Jeder Coding-Worker hat einen eigenen Container, persistenten State,
  Workspace, Unix-Socket und Docker-Bridge. Es gibt keine TCP-Ports, keinen
  Docker-Socket und keine State-/Workspace-Mounts zwischen den Workern.
- Jede generische Agent-Instanz besitzt dieselben Isolationsobjekte separat.
  Die private Registry enthält ihre Credential-Zuordnung; die an Direct/MCP
  weitergegebene Registry ist redigiert und read-only. Credential-Homes liegen
  außerhalb von State, Workspace, Context, Sockets, Jobs und Artefakten.
- Generische Rechte sind `inspect`, `edit`, `commandline`, `network`, `skills`,
  `agents-md`, `claude-md`. `edit` benötigt `inspect`; `commandline` benötigt
  `inspect+network`. Bei Codex ist aus technischen Gründen das gesamte Bündel
  `inspect+edit+commandline+network` erforderlich.
- Jeder Worker beginnt model-only und liest vor jedem Auftrag ein eigenes
  Profil mit `tools`, `commandline`, `skills`, `agents-md` und `claude-md`.
  Die Profildatei liegt außerhalb seines States und ist in diesem Worker nur
  read-only sichtbar; `commandline` benötigt `tools`.
- Geschützte Worker-Kontexte und Skills liegen unter `worker-context`, sind
  read-only gemountet und werden nur bei gesetztem Einzelschalter injiziert.
  Erkennt der Broker eine automatische Workspace-Kontext-/Skillquelle bei
  ausgeschaltetem Schalter, bricht er den Auftrag fail-closed ab.
- Codex, Claude und OpenCode erhalten jeweils eine CLI-native Tool- bzw.
  Permission-Konfiguration. Weitere MCPs, Plugins, Subagenten, Webtools und
  dynamische Skills bleiben in den Workern gesperrt.
- Hermes und die direkte Command-Sandbox sehen nur die Unix-Sockets der
  ausdrücklich freigegebenen Worker. Die Socket-Verzeichnisse sind für diese
  Clients read-only gemountet.
- Der Broker akzeptiert nur `status` und `run`, höchstens einen laufenden
  Auftrag, maximal 64 KiB Prompt, maximal 4 MiB Ausgabe und maximal 30 Minuten
  Laufzeit. Login, Logout, Modellwahl und Containerverwaltung bleiben
  Operator-Kommandos.
- Die generische Agent-RPC akzeptiert zusätzlich ausschließlich `cancel`.
  Der Agent-MCP exponiert genau neun Werkzeuge und keine Credential-, Login-,
  Docker-, Rechte- oder Team-Mutationsoperation.
- Artefakte sind auf 32 MiB begrenzt, tragen SHA-256 und werden vor dem Lesen
  geprüft. Modellzugriff ist auf 1 MiB und Text, JSON oder Git-Patches begrenzt.
  Team-DAGs werden vor Anwendung auf unbekannte Felder, Referenzen und Zyklen
  geprüft; Agenten teilen keinen implizit beschreibbaren Workspace.

Hermes' fest eingebauter Basis-Systemprompt ist weiterhin vorhanden. Entfernt
werden die persönlichen und projektbezogenen Zusatzquellen, nicht der
Agent-Kern selbst.

## Wichtige Restrisiken

1. **Der Docker-Socket ist eine starke Berechtigung.** Ein vollständiger
   Kompromiss des Hermes-Controller-Prozesses kann damit faktisch den Docker-
   Host kontrollieren. Die Trennung schützt vor normalen Modell-Toolcalls,
   nicht vor einer Remote-Code-Execution-Lücke im Controller. Für höhere
   Assurance sollte der Docker-Daemon in einer separaten VM oder rootless auf
   einem eigenen Host laufen.

2. **`web` und `shell-network` erlauben Egress.** Inhalte aus Prompt,
   Workspace oder Toolausgaben können dann an externe Ziele gesendet werden.
   Beide Rechte nur für passende Aufgaben aktivieren und danach entziehen.

3. **Der Modellprovider sieht die Inferenzdaten.** Docker-Isolation ändert
   nichts an der Datenschutzbeziehung zum ausgewählten API-Provider.

4. **Skills sind Anweisungen und Code.** Mit `skills` kann der Agent Skills
   verwalten. Zusammen mit `commandline` können Skill-Skripte in der Sandbox
   laufen. Deshalb verlangt die Basiskonfiguration eine Freigabe für
   Skill-Schreibvorgänge.

5. **Der Workspace ist absichtlich veränderbar.** `files`, `commandline` und
   `code` dürfen den Inhalt von `runtime/workspace` verändern. Dort keine
   Secrets ablegen. `.env`-Dateien werden von aktuellen Hermes-Dateitools zwar
   blockiert, eine Shell ist aber grundsätzlich mächtiger als ein Dateitool.

6. **Operator-Kommandos sind außerhalb der Agent-Policy.** `hermesctl auth` und
   `hermesctl skills` sowie die lokale `login-ui` führen bewusst administrative
   Kommandos aus. Wer
   Compose direkt startet oder Dateien in `runtime/state` ändert, kann die
   Hülle ebenfalls umgehen.

7. **Upstream-Updates brauchen Re-Validierung.** Das Image ist auf einen
   getesteten Digest festgelegt. Nach einer bewussten Änderung von
   `HERMES_IMAGE` immer `./hermesctl verify` ausführen.

8. **`hermesctl-direct` und `hermesctl-mcp` sind Meta-Berechtigungen.** Wer sie
   aktiviert, erlaubt dem Modell, die Capability-Liste dieser Installation für
   zukünftige Sitzungen und die separaten Profile der drei Worker für deren
   nächste Aufträge zu ändern. Die `AGENTS.md` verlangt vorher einen
   ausdrücklichen Benutzerwunsch, aber Prompt-Anweisungen sind keine harte
   Autorisierungsgrenze. Für rein operatorgesteuerte Rechte beide Schalter
   deaktiviert lassen.

9. **Ein Worker-`run` erlaubt immer eine Modellanfrage.** Ohne Worker-Rechte
   bleibt sie model-only; Provider und übermittelte Prompts sind dennoch eine
   externe Vertrauensgrenze. Mit `tools` kann der Worker Dateien verarbeiten,
   mit `commandline` Programme ausführen. Prompt Injection in Worker-Dateien
   wird dann relevant.

10. **Worker- und Broker-Credentials müssen im zugewiesenen Container lesbar
    sein.** Der jeweilige CLI-
    Prozess benötigt seinen persistenten Login-State. Ein Kompromiss dieses
    einen Worker-Containers kann deshalb dessen Account-Token und Workspace
    betreffen. Getrennte States und Sockets begrenzen dies auf diesen Worker,
    machen das Token aber nicht innerhalb desselben Containers geheim.
    Insbesondere Datei- oder Shell-Rechte sind daher keine Isolation vom
    Login-State derselben CLI. Der projektinterne Credential-Broker ist eine
    Mount-/Zuordnungsgrenze zwischen Instanzen, kein verschlüsselnder Vault und
    kein Token-Proxy. Für hohe Assurance sind kurzlebige, geringprivilegierte
    Accounts oder ein externer Secret-/Credential-Broker nötig.

11. **Worker-Egress ist absichtlich unabhängig von `shell-network`.** Der
    netzlose Hermes-Command-Sandbox kann über einen Socket einen Worker
    beauftragen; dieser Worker braucht für seine Modell-API ein eigenes
    Egress-Netz. Die Docker-Bridge ist nicht auf Provider-Domains gefiltert.
    Inhalte seines privaten Workspaces können dadurch externe Ziele erreichen.
    Dort keine zusätzlichen Secrets ablegen.

12. **Die drei Toolmodelle sind nicht identisch.** Codex bietet keine separat
    schaltbaren nativen Datei- und Shell-Tools. Seine innere read-only-
    `bubblewrap`-Sandbox kann im `cap_drop:ALL`-Container nicht initialisieren;
    deshalb bleibt die Shell ohne `commandline` aus und nutzt mit
    `tools+commandline` ausschließlich die äußere Docker-Isolation. Generische
    Codex-Instanzen erzwingen dafür das gesamte Arbeits-/Netz-Bündel. Claude
    trennt Dateiwerkzeuge und Bash. OpenCode trennt
    seine Permissions, aber Bash läuft im Worker mit dessen Egress. Die
    Statusausgabe benennt diese Abbildung ausdrücklich.

    `hermesctl access <target> explain` und das gleichnamige MCP-Werkzeug
    kennzeichnen deshalb native Abbildungen mit `[native]`, kontrollierte
    Kontextinjektion mit `[controlled]` und Codex-Abweichungen mit `[special]`.
    Die gemeinsame Benennung ist kein Versprechen identischer Technik.

13. **Kontextdateien und Skills sind Anweisungen, keine Sandbox.** Ihre
    read-only Herkunft verhindert Selbständerung, nicht Prompt Injection im
    Inhalt. Vor Aktivierung `worker-context/<worker>` prüfen. Der Broker lädt
    genehmigte `SKILL.md`-Inhalte kontrolliert als Kontext und lässt die
    dynamischen nativen Skill-/Plugin-Flächen gesperrt.

14. **Die Login-UI ist eine sensible lokale Operator-Oberfläche.** Loopback
    verhindert keinen Zugriff durch andere bereits kompromittierte Prozesse
    desselben Benutzerkontos. Der Bearer-Token steht absichtlich im startenden
    Terminal und zunächst im URL-Fragment; Browser-JavaScript entfernt ihn aus
    der Adresszeile. Während einer Login-Sitzung können Einmalcodes und URLs in
    der flüchtigen, auf 1 MiB begrenzten Ausgabe erscheinen. Server nach der
    Anmeldung mit `Ctrl-C` beenden, URL und Token nicht teilen und die UI nicht
    durch Reverse Proxy, Portweiterleitung oder Container-Portmapping nach
    außen veröffentlichen. Die dauerhaften Provider-Tokens werden nicht von
    der UI gelesen, liegen aber weiterhin im jeweiligen Worker-State.

15. **`network` ist eine ausdrückliche Risikobestätigung, keine Domain-
    Firewall.** Coding-CLIs brauchen Provider-Egress. Aktivierte Bash läuft im
    selben Container und kann deshalb dieselbe, nicht auf Provider-Domains
    gefilterte Bridge verwenden. Die Policy verlangt `network` vor
    `commandline`, trennt den Verkehr aber nicht technisch nach Ziel-Domain.

16. **Persistente Jobs sind kein hochverfügbarer Queue-Dienst.** Status und
    Artefakte überleben den aufrufenden Prozess, ein Host-Neustart kann einen
    gerade laufenden Hintergrundjob jedoch ohne Abschlusszustand zurücklassen.
    Für verteilte oder hochverfügbare Ausführung ist ein externer Queue-/Runner-
    Dienst nötig. Teamläufe stoppen fail-closed beim ersten blockierten oder
    fehlgeschlagenen Schritt.

17. **Geteilte Broker-Credentials schwächen die Isolation.** Sie sind nur mit
    einem expliziten `--share-credential`/`--share` möglich. Gleichzeitig
    laufende CLIs können denselben Token-State aktualisieren oder sperren. Pro
    Agent ein eigenes Credential ist der sichere Standard.

18. **Evaluationen sind reine Operator-Aktionen.** Weder Hermes-MCP noch
    Agent-Skills erhalten Create/Run/Cancel/Export-Operationen. Die lokale
    Oberfläche bindet ausschließlich an `127.0.0.1`, verlangt einen zufälligen
    Bearer-Token, prüft Origin und sendet CSP-, Frame- und No-Store-Header.

19. **Trial-Eingaben werden vor dem Lauf snapshotiert.** Manifeste akzeptieren
    nur explizite reguläre Dateien und Verzeichnisse. Symlinks, Spezialdateien,
    Pfadtraversierung, mehr als 10.000 Dateien oder mehr als 32 MiB pro Snapshot
    werden abgewiesen. Archive werden einzeln in einen validierten temporären
    Agent-Pfad geschrieben; `extractall` wird nicht verwendet. Kanonische
    Worker- und Quellagent-Kontexte werden nicht verändert.

20. **Evaluationen teilen ausschließlich das ausgewählte Credential.** Jeder
    Trial besitzt ansonsten eigenen State, Workspace, Kontext, Socket und
    Container. Manifest v1 erzwingt einen Trial je Credential, um parallele
    Token-Refresh-Rennen innerhalb eines Experiments zu vermeiden. Eine
    gleichzeitig außerhalb der Evaluation gestartete Nutzung muss der
    Operator weiterhin ausschließen.

21. **Trial-Aufräumen erfolgt fail-closed.** Vor dem Löschen werden sämtliche
    Rechte entfernt und der Docker-Stack gestoppt. Scheitert der Stopp, bleiben
    Registry und Diagnosezustand erhalten. `resume` bereinigt einen nach einem
    Prozessabbruch als laufend markierten Trial vor der Wiederholung.

22. **Kosten sind vom Abrechnungsmodus abhängig.** Providerwerte werden nur
    bei `billing_mode: api` als `reported_cost_usd` übernommen. Monatsabos und
    lokale Modelle erhalten keinen erfundenen Kostenwert. Roh- und
    normalisierte Antworten sind Operator-Artefakte und können sensible
    Modellausgaben enthalten.

## Empfohlene Freigabereihenfolge

1. Nullzustand (`reset`) und reine Dialogqualität prüfen.
2. `planning` für interne Aufgabenstruktur.
3. `files` mit Testdaten im isolierten Workspace.
4. `commandline` weiterhin ohne Netz.
5. `skills`, zunächst ohne Commandline, Inhalte prüfen.
6. `web` oder `shell-network` nur für konkrete Aufgaben und zeitlich begrenzt.
7. `orchestrator`/`agents-md` und `claude-md` jeweils erst nach Prüfung der
   zugehörigen Datei.
8. `hermesctl-direct` oder `hermesctl-mcp` nur dann, wenn Hermes seine
   zukünftigen Rechte tatsächlich selbst verwalten soll.
9. Einen Worker zuerst als Operator anmelden und mit `worker ... status`
   sowie `worker ... rights` prüfen; anschließend genau einen `*-mcp`-Schalter
   testen. Der Worker bleibt dabei model-only.
10. Für genau diesen Worker über `access <worker>` zuerst `agents-md` oder
    `skills`, dann `tool-use` und zuletzt bei Bedarf `commandline` einzeln
    freigeben.
11. `*-direct` nur ergänzen, wenn der direkte Commandline-Weg wirklich nötig
    ist. Weitere Worker einzeln und erst nach Prüfung ihres privaten Workspaces
    freigeben.
12. Generische Agenten zuerst mit eigener Broker-Zuordnung und leerem Profil
    erstellen; bei Claude/OpenCode `inspect`, dann `edit`, zuletzt
    `commandline+network` prüfen. Codex nur nach Erklärung seines vollständigen
    `[special]`-Bündels freigeben.
13. Teams zunächst mit `team apply --explain` prüfen. MCP erst danach mit
    `agents-mcp`; `agents-direct` nur wenn der direkte Weg erforderlich ist.
14. Evaluationen zuerst mit `benchmark plan` prüfen. Manifestpfade und
    Context-/Workspace-Quellen nur aus kontrollierten Verzeichnissen verwenden.
