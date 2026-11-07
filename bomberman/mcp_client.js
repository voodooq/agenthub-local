import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { spawn } from "child_process";

export async function createLocalResourceClient() {
    const transport = new StdioClientTransport({
        command: "node",
        args: ["../skills/local-resource-viewer/server.js"]
    });
    const client = new Client({ name: "bomberman-mcp-client", version: "1.0.0" }, { capabilities: {} });
    await client.connect(transport);
    return client;
}
