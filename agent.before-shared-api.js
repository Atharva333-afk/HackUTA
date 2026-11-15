require("dotenv").config({ path: ".env.local" });

const { execFile } = require("child_process");
const { promisify } = require("util");
const execFileAsync = promisify(execFile);

const {
  cli,
  defineAgent,
  ServerOptions,
  voice,
  inference,
} = require("@livekit/agents");



const agent = defineAgent({
  entry: async (ctx) => {
    console.log("English Speaker agent received a call");

    const session = new voice.AgentSession({
      stt: new inference.STT({
        model: "deepgram/nova-3-general",
        language: "en",
      }),

      tts: new inference.TTS({
        model: "cartesia/sonic-3",
        voice: "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        language: "en",
      }),

      turnHandling: {
        turnDetection: "stt",
      },

      vad: null,
    });

    session.on(
      voice.AgentSessionEventTypes.UserInputTranscribed,
      async (event) => {
        if (!event.isFinal) return;

        const transcript = event.transcript?.trim();

        if (!transcript) return;

        console.log("");
        console.log("========== TRANSCRIPT ==========");
        console.log(transcript);
        console.log("================================");
        console.log("");

        try {
          console.log(">>> CALLING FREELLM NOW");

          const messages = [
            {
              role: "system",
              content: `You are the language-processing layer of a real-time accessibility system helping an English speaker communicate with an ASL user.

Do not answer the speaker.

Return ONLY valid JSON using exactly:
{
  "source": "english",
  "original_text": "...",
  "clean_text": "...",
  "intent": "..."
}

Preserve the original meaning.
Remove filler or accidental repetition only when appropriate.
Do not invent information.

Intent must be exactly one of:
"statement", "question", "command", "greeting", "other".

Do not use Markdown or code fences.`,
            },
            {
              role: "user",
              content: transcript,
            },
          ];

          const { stdout } = await execFileAsync("curl", [
            "--max-time", "12",
            "-sS",
            "-H", "Authorization: Bearer " + process.env.FREELLM_API_KEY,
            "-H", "Content-Type: application/json",
            process.env.FREELLM_BASE_URL + "/chat/completions",
            "-d", JSON.stringify({
              model: "gemini-3.6-flash",
              messages,
              temperature: 0.1,
            }),
          ]);

          const response = JSON.parse(stdout);

          if (response.error) {
            throw new Error(response.error.message || "FreeLLM provider error");
          }

          const result =
            response.choices?.[0]?.message?.content?.trim();

          if (!result) {
            console.log("FreeLLM returned an empty response.");
            return;
          }

          console.log("========== FREELLM ==========");
          console.log(result);
          console.log("=============================");
          console.log("");

          try {
            const structuredMessage = JSON.parse(result);

            console.log("========== ASL MESSAGE ==========");
            console.log(
              "Original:",
              structuredMessage.original_text
            );

            console.log(
              "Clean:",
              structuredMessage.clean_text
            );

            console.log(
              "Intent:",
              structuredMessage.intent
            );

            console.log("=================================");
            console.log("");

            const payload = new TextEncoder().encode(
              JSON.stringify(structuredMessage)
            );

            await ctx.room.localParticipant.publishData(payload, {
              reliable: true,
              topic: "english-to-asl",
            });

            console.log("Sent message to ASL channel.");
            console.log("");

            /*
              NEXT STEP:

              structuredMessage contains:

              {
                source: "english",
                original_text: "...",
                clean_text: "...",
                intent: "..."
              }

              Later this will be sent to the ASL-side agent.
            */

          } catch (jsonError) {
            console.error("");
            console.error("========== JSON ERROR ==========");
            console.error("FreeLLM returned invalid JSON.");
            console.error(result);
            console.error("================================");
            console.error("");
          }

        } catch (error) {
          console.error("");
          console.error("========== FREELLM ERROR ==========");
          console.error(error.message || error);
          console.error("===================================");
          console.error("");
        }
      }
    );

    session.on(
      voice.AgentSessionEventTypes.Error,
      (event) => {
        console.error("");
        console.error("========== SESSION ERROR ==========");
        console.error(event);
        console.error("===================================");
        console.error("");
      }
    );

    await session.start({
      agent: voice.Agent.create({
        instructions:
          "Listen to the English speaker and transcribe their speech accurately.",
      }),

      room: ctx.room,
    });

    await ctx.connect();

    const greeting = session.say("Hi, I am ready.", {
      allowInterruptions: false,
    });

    await greeting.waitForPlayout();

    console.log("");
    console.log("==========================================");
    console.log("ENGLISH SPEAKER AGENT READY");
    console.log("FaceTime -> Speech -> Text -> FreeLLM");
    console.log("Model chain: auto:asl-realtime");
    console.log("==========================================");
    console.log("");
  },
});

module.exports = agent;

if (require.main === module) {
  cli.runApp(
    new ServerOptions({
      agent: __filename,
      agentName: "english-speaker-agent",
    })
  );
}