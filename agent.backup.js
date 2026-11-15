require("dotenv").config({ path: ".env.local" });

const OpenAI = require("openai");

const {
  cli,
  defineAgent,
  ServerOptions,
  voice,
  inference,
} = require("@livekit/agents");

const client = new OpenAI({
  apiKey: process.env.FREELLM_API_KEY,
  baseURL: process.env.FREELLM_BASE_URL,
});

const agent = defineAgent({
  entry: async (ctx) => {
    console.log("English Speaker agent received a call");

    const session = new voice.AgentSession({
      stt: new inference.STT({
        model: "deepgram/nova-3-general",
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
          const response = await client.chat.completions.create({
            model: "auto:asl-realtime",

            messages: [
              {
                role: "system",
                content: `
You are the language-processing layer of a real-time accessibility
system that helps an English-speaking person communicate with an
American Sign Language user.

Your job is NOT to answer the English speaker.

Your job is to understand exactly what the speaker said and prepare
a clean structured message that can later be sent to the ASL side
of the system.

Preserve the speaker's original meaning.

Remove speech disfluencies only when they do not affect meaning,
such as filler words, false starts, or accidental repetition.

Do not invent information.

Do not unnecessarily rewrite simple sentences.

Return ONLY valid JSON.

Use exactly this JSON structure:

{
  "source": "english",
  "original_text": "...",
  "clean_text": "...",
  "intent": "..."
}

Rules:

"original_text"
- Preserve the original transcript.

"clean_text"
- Produce the clearest natural-English version of the same message.
- Keep the meaning unchanged.
- Remove unnecessary filler words when appropriate.

"intent"
Choose exactly one of these:

"statement"
"question"
"command"
"greeting"
"other"

Do not use Markdown.
Do not use code fences.
Do not provide explanations.
Do not put anything before or after the JSON object.
                `.trim(),
              },

              {
                role: "user",
                content: transcript,
              },
            ],

            temperature: 0.1,
          });

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