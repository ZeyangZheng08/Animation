using System;
using System.Diagnostics;
using System.IO;
using UnityEditor;
using UnityEngine;
using Debug = UnityEngine.Debug;

namespace AgentRuntime.EditorTools
{
    /// <summary>
    /// Opens the console terminal when you press Play.
    ///
    /// The console is a separate Windows process now — it talks to the agent in WSL over the console
    /// channel, and Unity is a third party to that conversation. Which is right, but it left a hole: the
    /// in-editor window used to appear with play mode, and after it was removed, pressing Play gave a
    /// running scene and nowhere to type. This closes that without putting the console back in the
    /// engine: Unity launches the terminal and then has nothing more to do with it.
    ///
    /// It launches at most one. `terminal.ps1` starts the agent service if it is not already up and
    /// attaches; a second window would attach a second console to the same turn, which works but is not
    /// what pressing Play twice should mean.
    ///
    /// EDITOR ONLY, AND NOT PART OF THE RUNTIME CONTRACT. Nothing in a build depends on this, the
    /// executor does not know it exists, and turning it off changes nothing except that you start the
    /// terminal yourself.
    /// </summary>
    [InitializeOnLoad]
    public static class AgentTerminal
    {
        private const string ScriptKey = "AgentRuntime.TerminalScript";
        private const string OnPlayKey = "AgentRuntime.TerminalOnPlay";
        private const string DefaultScript =
            @"\\wsl.localhost\Ubuntu-24.04\home\chenhui\Research\animation-agent\terminal.ps1";

        private static Process _terminal;

        static AgentTerminal()
        {
            EditorApplication.playModeStateChanged += state =>
            {
                if (state == PlayModeStateChange.EnteredPlayMode && OnPlay) Open();
            };
        }

        /// <summary>Where terminal.ps1 lives, as a Windows path. Per machine, not committed — the agent
        /// repository sits inside WSL and its path is nobody else's.</summary>
        public static string ScriptPath
        {
            get { return EditorPrefs.GetString(ScriptKey, DefaultScript); }
            set { EditorPrefs.SetString(ScriptKey, value); }
        }

        public static bool OnPlay
        {
            get { return EditorPrefs.GetBool(OnPlayKey, true); }
            set { EditorPrefs.SetBool(OnPlayKey, value); }
        }

        [MenuItem("Tools/Animation Agent/Open Terminal %#a")]
        public static void Open()
        {
            if (_terminal != null && !SafeHasExited(_terminal))
            {
                // Already attached. Bring nothing to the front: stealing focus from the game view in the
                // middle of a run is worse than making you click the taskbar.
                return;
            }

            string script = ScriptPath;
            if (!File.Exists(script))
            {
                Debug.LogWarning("[AgentRuntime] no terminal launcher at " + script +
                                 "\nSet it with Tools > Animation Agent > Set Terminal Script, or start " +
                                 "one yourself:\n  powershell -ExecutionPolicy Bypass -File <path>\\terminal.ps1");
                return;
            }

            // NO -NoExit. With it the window outlived the run: terminal.ps1 ends when the service
            // does — which is when play mode stops — and PowerShell then sat at a prompt forever.
            // Nothing closed it, because there is no exit-play-mode hook here and adding one would
            // mean killing a process this class may no longer own (wt.exe hands off to a running
            // instance, so the handle is often already dead). Letting the script's own exit close
            // its window is the same behaviour with nobody having to track anything.
            //
            // Failures stay readable: terminal.ps1 waits for a keypress before returning non-zero,
            // so a window that vanishes means the run ended normally and one that stays is telling
            // you something.
            string arguments = "-ExecutionPolicy Bypass -File \"" + script + "\"";
            try
            {
                // Windows Terminal when it is there, because a UNC-launched console host is cramped and
                // this one scrolls a transcript. Plain powershell.exe otherwise; both end up running the
                // same script.
                ProcessStartInfo info = HasWindowsTerminal()
                    ? new ProcessStartInfo("wt.exe", "powershell.exe " + arguments)
                    : new ProcessStartInfo("powershell.exe", arguments);
                info.UseShellExecute = true;
                _terminal = Process.Start(info);
            }
            catch (Exception e)
            {
                Debug.LogWarning("[AgentRuntime] could not open the terminal: " + e.Message);
            }
        }

        [MenuItem("Tools/Animation Agent/Open Terminal On Play")]
        private static void ToggleOnPlay()
        {
            OnPlay = !OnPlay;
        }

        [MenuItem("Tools/Animation Agent/Open Terminal On Play", true)]
        private static bool ToggleOnPlayValidate()
        {
            Menu.SetChecked("Tools/Animation Agent/Open Terminal On Play", OnPlay);
            return true;
        }

        [MenuItem("Tools/Animation Agent/Set Terminal Script...")]
        private static void SetScript()
        {
            string chosen = EditorUtility.OpenFilePanel("terminal.ps1", Path.GetDirectoryName(ScriptPath) ?? "", "ps1");
            if (!string.IsNullOrEmpty(chosen)) ScriptPath = chosen.Replace('/', '\\');
        }

        private static bool HasWindowsTerminal()
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return File.Exists(Path.Combine(local, @"Microsoft\WindowsApps\wt.exe"));
        }

        private static bool SafeHasExited(Process p)
        {
            try { return p.HasExited; }
            catch (InvalidOperationException) { return true; }   // never started, or already reaped
        }
    }
}
