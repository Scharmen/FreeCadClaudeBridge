# ClaudeBridge: start the bridge server and chat panel once the GUI is up.
import FreeCADGui as Gui


class ClaudeBridgeWorkbench(Gui.Workbench):
    MenuText = "Claude Bridge"
    ToolTip = "Status and controls of the Claude Code bridge (not needed for normal use)"

    def Initialize(self):
        from claudebridge import addon

        self.appendMenu("Claude", addon.COMMANDS)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(ClaudeBridgeWorkbench())


def _claude_bridge_boot():
    from claudebridge import addon

    addon.boot()


_claude_bridge_boot()
