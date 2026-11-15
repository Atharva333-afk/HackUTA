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
const elevenlabs = require("@livekit/agents-plugin-elevenlabs");

// The ElevenLabs CLI stores the key as ELEVENLABS_API_KEY; the LiveKit plugin's
// own default name is ELEVEN_API_KEY. Accept either so the key is set only once.
const ELEVENLABS_API_KEY =
  process.env.ELEVENLABS_API_KEY || process.env.ELEVEN_API_KEY;

// George, a pre-made voice every ElevenLabs plan can use through the API.
const ELEVENLABS_VOICE_ID =
  process.env.ELEVENLABS_VOICE_ID ||
  process.env.ELEVEN_VOICE_ID ||
  "JBFqnCBsd6RMkjVDRZzb";

// Speech -> text for the hearing person. ElevenLabs Scribe (realtime) by default;
// set STT_PROVIDER=deepgram in .env.local to fall back to the original Deepgram setup.
function createSpeechToText() {
  if (process.env.STT_PROVIDER === "deepgram") {
    console.log("Speech to text: Deepgram (fallback)");
    return new inference.STT({
      model: "deepgram/nova-3-general",
      language: "en",
    });
  }

  console.log("Speech to text: ElevenLabs Scribe realtime");
  return new elevenlabs.STT({
    apiKey: ELEVENLABS_API_KEY,
    model: "scribe_v2_realtime",
    languageCode: "en",
    // Don't add sound tags like "(laughs)" to the captions.
    tagAudioEvents: false,
    // Scribe ends a sentence after ~1 second of silence, so each spoken
    // sentence becomes one final transcript (walkie-talkie turns).
    serverVad: { vadSilenceThresholdSecs: 1.0 },
  });
}

// How often to check the shared server for new ASL translations (ms).
const ASL_POLL_INTERVAL_MS = 1000;

// Reads ASL -> English messages from the shared server and speaks each new one
// into the call with the ElevenLabs voice. Returns a function that stops the loop.
function startAslSpeaker(session) {
  const baseUrl = process.env.SHARED_API_URL;
  const token = process.env.SHARED_API_TOKEN;

  if (!baseUrl || !token) {
    console.log("SHARED_API_URL or SHARED_API_TOKEN missing; ASL voice is off.");
    return () => {};
  }

  let lastId = null; // null until we skip the backlog from before this call
  let stopped = false;
  let busy = false;

  const fetchAsl = async (after) => {
    const response = await fetch(`${baseUrl}/messages/asl?after=${after}`, {
      headers: { "X-API-Token": token },
    });
    if (!response.ok) {
      throw new Error(`Shared server returned ${response.status}`);
    }
    return response.json();
  };

  const tick = async () => {
    if (stopped || busy) return;
    busy = true;
    try {
      if (lastId === null) {
        // First run: do not read out messages sent before this call started.
        const backlog = await fetchAsl(0);
        lastId = backlog.length ? backlog[backlog.length - 1].id : 0;
        return;
      }

      const fresh = await fetchAsl(lastId);
      for (const message of fresh) {
        lastId = message.id;
        const text = message.clean_text?.trim();
        if (!text) continue;

        console.log("");
        console.log("========== SPEAKING ASL ==========");
        console.log(text);
        console.log("==================================");

        const handle = session.say(text, { allowInterruptions: false });
        await handle.waitForPlayout();
      }
    } catch (error) {
      console.error("ASL voice error:", error.message || error);
    } finally {
      busy = false;
    }
  };

  const timer = setInterval(tick, ASL_POLL_INTERVAL_MS);
  tick();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}



const agent = defineAgent({
  entry: async (ctx) => {
    console.log("English Speaker agent received a call");

    const session = new voice.AgentSession({
      stt: createSpeechToText(),

      // ElevenLabs voice for the signer (sign -> voice).
      tts: new elevenlabs.TTS({
        apiKey: ELEVENLABS_API_KEY,
        voiceId: ELEVENLABS_VOICE_ID,
        model: process.env.ELEVENLABS_TTS_MODEL || "eleven_flash_v2_5",
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

            try {
              const response = await fetch(
                `${process.env.SHARED_API_URL}/messages`,
                {
                  method: "POST",
                  headers: {
                    "Content-Type": "application/json",
                    "X-API-Token": process.env.SHARED_API_TOKEN,
                  },
                  body: JSON.stringify(structuredMessage),
                }
              );

              const sharedResult = await response.json();
              console.log("Sent message to shared backend:", sharedResult);
            } catch (error) {
              console.error("Shared backend error:", error.message);
            }
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

    // Sign -> voice: speak each new ASL translation into the call.
    const stopAslSpeaker = startAslSpeaker(session);
    ctx.addShutdownCallback(async () => stopAslSpeaker());

    console.log("");
    console.log("==========================================");
    console.log("ENGLISH SPEAKER AGENT READY");
    console.log("FaceTime -> Scribe speech to text -> FreeLLM");
    console.log("ASL text -> ElevenLabs voice -> FaceTime");
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