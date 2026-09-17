// Native-process fixture for Windows PowerShell 5.1 launcher regression tests.
// It never talks to a real Docker daemon or reads user credentials.
using System;
using System.IO;

public static class MockDocker
{
    public static int Main(string[] args)
    {
        string log = Environment.GetEnvironmentVariable("QQBOT_TEST_DOCKER_LOG");
        string state = Environment.GetEnvironmentVariable("QQBOT_TEST_DOCKER_STATE");
        string mode = Environment.GetEnvironmentVariable("QQBOT_TEST_DOCKER_MODE") ?? "";
        if (String.IsNullOrEmpty(log) || String.IsNullOrEmpty(state)) return 99;
        File.AppendAllText(log, String.Join(" ", args) + Environment.NewLine);
        if (args.Length == 0) return 98;
        if (args[0] == "version") { Console.WriteLine("29.0-test"); return 0; }
        if (args[0] == "image")
        {
            if (mode == "query-fail") { Console.Error.WriteLine("Docker unavailable (fixture)"); return 1; }
            string id = File.ReadAllText(state).Trim();
            if (args[1] == "inspect" && id.Length == 0)
            {
                Console.Error.WriteLine("Error response from daemon: No such image: local/nonebot-qq:1.7.2");
                return 1;
            }
            if (id.Length > 0) Console.WriteLine(id);
            return 0;
        }
        if (args[0] == "load")
        {
            if (mode == "load-fail") { Console.Error.WriteLine("Invalid archive (fixture)"); return 1; }
            File.WriteAllText(state, Environment.GetEnvironmentVariable("QQBOT_TEST_DOCKER_LOAD_ID") ?? "");
            Console.WriteLine("Loaded image: local/nonebot-qq:1.7.2");
            return 0;
        }
        if (args[0] == "inspect") { Console.WriteLine("running|healthy"); return 0; }
        if (args[0] == "compose")
        {
            if (Array.IndexOf(args, "ps") >= 0) Console.WriteLine("test-container");
            if (Array.IndexOf(args, "version") >= 0) Console.WriteLine("Docker Compose test");
            return 0;
        }
        return 97;
    }
}
