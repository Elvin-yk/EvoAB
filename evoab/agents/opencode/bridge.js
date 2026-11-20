// Adapter hooks contain runtime identity; robot schemas remain unchanged.
export const RobotBridge = async () => ({
  "experimental.chat.system.transform": async (_input, output) => {
    output.system = [process.env.EVOAB_SYSTEM_PROMPT];
  },
  "tool.execute.before": async (input, output) => {
    if (input.tool.startsWith("ura_")) {
      if (!input.callID) throw new Error("OpenCode omitted its tool call ID");
      output.args._evoab_call = input.callID;
    }
  },
  "tool.execute.after": async (input) => {
    if (input.tool.startsWith("ura_")) delete input.args._evoab_call;
  },
  "experimental.chat.messages.transform": async (_input, output) => {
    // OpenCode can persist arguments before the after hook, including on errors.
    // Remove transport metadata from those snapshots before model serialization.
    for (let index = 0; index < output.messages.length; index++) {
      const message = output.messages[index];
      output.messages[index] = { ...message, parts: message.parts.map((part) => {
        if (part.type !== "tool" || !part.tool.startsWith("ura_")) return part;
        const args = { ...part.state.input };
        delete args._evoab_call;
        return { ...part, state: { ...part.state, input: args } };
      }) };
    }
  },
});
