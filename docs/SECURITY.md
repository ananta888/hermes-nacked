# Sicherheitsmodell und Grenzen

## Vertrauensgrenzen

```text
Benutzer
  └─ Hermes-Controller (Docker, Providerzugang, kein Host-Projekt)
       ├─ optional: read-only AGENTS.md (orchestrator)
       ├─ optional: lokaler hermesctl-MCP (nur Capability-Policy)
       ├─ optional: eng begrenzte Worker-MCPs (je nur status/run)
       ├─ optional: read-only Unix-Socket des freigegebenen Workers
       ├─ Modell-API (nur für Inferenz)
       └─ optional: Docker-Socket
            └─ Command-Sandbox (nur Workspace, standardmäßig ohne Netzwerk)

  ├─ Codex-Worker (eigener State, Workspace, Socket und Egress)
  ├─ Claude-Worker (eigener State, Workspace, Socket und Egress)
  └─ OpenCode-Worker (eigener State, Workspace, Socket und Egress)
```

Im Nullzustand wird der Docker-Socket nicht gemountet. Er kommt erst hinzu,
wenn `files`, `commandline` oder `code` aktiv ist. Der Agent erhält den Socket
nie als Datei oder Environmentvariable; Hermes benutzt ihn als Controller, um
den zweiten Sandbox-Container zu verwalten.

## Garantien dieses Projekts

- Unbekannte Capability-Namen brechen den Start ab (fail closed).
- Ohne Capability ist die effektive Toolliste leer.
- Provider-Credentials werden nicht an die Command-Sandbox weitergereicht.
- Nur `runtime/workspace` wird schreibbar in die Command-Sandbox gemountet.
- Skill-, Credential- und Mediencache-Mounts werden standardmäßig unterdrückt.
- Skills werden nur gemountet, wenn `skills` aktiv ist; Skill-Credentials auch
  dann nicht automatisch.
- Kontextregeln werden standardmäßig nicht geladen. `orchestrator` bindet
  ausschließlich die projektgebundene `AGENTS.md` read-only ein; persistentes
  Memory bleibt deaktiviert.
- Plugin- und Hook-Discovery bleibt aus. MCP bleibt ebenfalls aus, außer eine
  explizite `*-mcp`-Capability ist aktiv; dann ist die Discovery auf die
  freigegebenen lokalen Server und deren exakte Toolnamen begrenzt.
- Der direkte `hermesctl`-Zugriff mountet nur den Control-Client, das Policy-
  Modul und die Capability-Datei in die netzlose Command-Sandbox. Docker-
  Administration und die Projektwurzel werden nicht sichtbar.
- Jeder Coding-Worker hat einen eigenen Container, persistenten State,
  Workspace, Unix-Socket und Docker-Bridge. Es gibt keine TCP-Ports, keinen
  Docker-Socket und keine State-/Workspace-Mounts zwischen den Workern.
- Hermes und die direkte Command-Sandbox sehen nur die Unix-Sockets der
  ausdrücklich freigegebenen Worker. Die Socket-Verzeichnisse sind für diese
  Clients read-only gemountet.
- Der Broker akzeptiert nur `status` und `run`, höchstens einen laufenden
  Auftrag, maximal 64 KiB Prompt, maximal 4 MiB Ausgabe und maximal 30 Minuten
  Laufzeit. Login, Logout, Modellwahl und Containerverwaltung bleiben
  Operator-Kommandos.

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
   `hermesctl skills` führen bewusst administrative Hermes-Kommandos aus. Wer
   Compose direkt startet oder Dateien in `runtime/state` ändert, kann die
   Hülle ebenfalls umgehen.

7. **Upstream-Updates brauchen Re-Validierung.** Das Image ist auf einen
   getesteten Digest festgelegt. Nach einer bewussten Änderung von
   `HERMES_IMAGE` immer `./hermesctl verify` ausführen.

8. **`hermesctl-direct` und `hermesctl-mcp` sind Meta-Berechtigungen.** Wer sie
   aktiviert, erlaubt dem Modell, die Capability-Liste dieser Installation für
   zukünftige Sitzungen zu ändern. Die `AGENTS.md` verlangt vorher einen
   ausdrücklichen Benutzerwunsch, aber Prompt-Anweisungen sind keine harte
   Autorisierungsgrenze. Für rein operatorgesteuerte Rechte beide Schalter
   deaktiviert lassen.

9. **Ein Worker-`run` ist eine starke Delegationsberechtigung.** Die RPC-
   Oberfläche hat nur zwei Operationen, aber die gestartete Coding-CLI kann in
   ihrem privaten Workspace Dateien ändern, Programme ausführen und über ihr
   eigenes Netz den Modellprovider erreichen. Prompt Injection in
   Worker-Dateien bleibt damit relevant.

10. **Worker-Credentials müssen im Worker lesbar sein.** Der jeweilige CLI-
    Prozess benötigt seinen persistenten Login-State. Ein Kompromiss dieses
    einen Worker-Containers kann deshalb dessen Account-Token und Workspace
    betreffen. Getrennte States und Sockets begrenzen dies auf diesen Worker,
    machen das Token aber nicht innerhalb desselben Containers geheim.

11. **Worker-Egress ist absichtlich unabhängig von `shell-network`.** Der
    netzlose Hermes-Command-Sandbox kann über einen Socket einen Worker
    beauftragen; dieser Worker braucht für seine Modell-API ein eigenes
    Egress-Netz. Die Docker-Bridge ist nicht auf Provider-Domains gefiltert.
    Inhalte seines privaten Workspaces können dadurch externe Ziele erreichen.
    Dort keine zusätzlichen Secrets ablegen.

## Empfohlene Freigabereihenfolge

1. Nullzustand (`reset`) und reine Dialogqualität prüfen.
2. `planning` für interne Aufgabenstruktur.
3. `files` mit Testdaten im isolierten Workspace.
4. `commandline` weiterhin ohne Netz.
5. `skills`, zunächst ohne Commandline, Inhalte prüfen.
6. `web` oder `shell-network` nur für konkrete Aufgaben und zeitlich begrenzt.
7. `orchestrator` erst nach Prüfung der `AGENTS.md`.
8. `hermesctl-direct` oder `hermesctl-mcp` nur dann, wenn Hermes seine
   zukünftigen Rechte tatsächlich selbst verwalten soll.
9. Einen Worker zuerst als Operator anmelden und mit `worker ... status`
   prüfen; anschließend genau einen `*-mcp`-Schalter testen.
10. `*-direct` nur ergänzen, wenn der direkte Commandline-Weg wirklich nötig
    ist. Weitere Worker einzeln und erst nach Prüfung ihres privaten Workspaces
    freigeben.
