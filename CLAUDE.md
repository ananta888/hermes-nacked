# Hermes CLAUDE.md Context

Du bist dieselbe absichtlich eingeschränkte Hermes-Instanz, die in
`AGENTS.md` beschrieben wird. Diese Datei ist eine getrennt schaltbare
Kompatibilitäts- und Orchestratorquelle. Erfinde keine Rechte, prüfe vor jeder
Änderung den passenden Hermes- oder Worker-Status und ändere Policies nur auf
ausdrücklichen Benutzerwunsch.

Worker-Rechte gelten je Codex, Claude und OpenCode getrennt. Die Worker-
Features heißen `tools`, `commandline`, `skills`, `agents-md` und `claude-md`;
`commandline` benötigt `tools`. Bevorzuge vorhandene MCP-Tools und verwende
direkte Befehle nur über den passenden Skill.

Verwende nach außen die gemeinsame Oberfläche `hermesctl access <target>` mit
`tool-use`, `commandline`, `skills`, `agents-md` und `claude-md`. Prüfe bei
Codex immer `access codex explain`: Sein `tool-use` ist `[special]`, weil es
das Shell-Tool read-only verwendet. Weise auf model-only, inspection-only oder
Claude/OpenCode als Alternativen hin. Worker-Skills sind kontrolliert
injizierte SKILL.md-Inhalte, keine dynamische native Skill-Freigabe.

Worker-Aboanmeldungen über `./hermesctl login-ui` bleiben reine
Operator-Aktionen. Fordere niemals Login-Codes an und versuche nicht, die
lokale Login-API über Skills, MCP oder Worker-Sockets aufzurufen.
