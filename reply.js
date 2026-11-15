// Hearing person's reply without a live call: transcribes a recorded voice
// clip with ElevenLabs Scribe and sends it to the signer through the shared server.
//
// Usage:
//   node reply.js reply.m4a          -> Scribe transcribes the clip, then it's sent
//   node reply.js --text "Hi there"  -> sends typed text (no transcription)
require("dotenv").config({ path: ".env.local" });

const fs = require("fs");
const { ElevenLabsClient } = require("@elevenlabs/elevenlabs-js");

async function transcribe(filePath) {
  const client = new ElevenLabsClient();
  const result = await client.speechToText.convert({
    file: fs.createReadStream(filePath),
    modelId: "scribe_v3",
    languageCode: "en",
    tagAudioEvents: false,
  });
  return (result.text || "").trim();
}

async function sendToSigner(text) {
  const response = await fetch(`${process.env.SHARED_API_URL}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Token": process.env.SHARED_API_TOKEN,
    },
    body: JSON.stringify({
      source: "english",
      original_text: text,
      clean_text: text,
      intent: text.endsWith("?") ? "question" : "statement",
    }),
  });
  if (!response.ok) throw new Error(`Shared server returned ${response.status}`);
  return response.json();
}

async function main() {
  const args = process.argv.slice(2);
  let text;
  if (args[0] === "--text") {
    text = args.slice(1).join(" ").trim();
  } else if (args[0]) {
    console.log("Transcribing with ElevenLabs Scribe...");
    text = await transcribe(args[0]);
  }
  if (!text) {
    console.log('Usage: node reply.js reply.m4a   or   node reply.js --text "message"');
    process.exit(1);
  }
  console.log(`Hearing person said: "${text}"`);
  await sendToSigner(text);
  console.log("Sent to the signer's screen.");
}

main().catch((error) => {
  console.error("Reply failed:", error.message || error);
  process.exit(1);
});
