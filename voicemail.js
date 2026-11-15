// Signed voicemail: turns a signed message's English text into a voice clip
// with ElevenLabs, saved as an .mp3 the signer can send over iMessage.
//
// Usage:
//   node voicemail.js "I love you, see you Sunday"   -> clip of that text
//   node voicemail.js --latest                       -> clip of the newest ASL translation
//   add --play to hear it right away (macOS)
//
// The key is read from .env.local (ELEVENLABS_API_KEY), never from the code.
require("dotenv").config({ path: ".env.local" });

const fs = require("fs");
const path = require("path");
const { Readable } = require("stream");
const { pipeline } = require("stream/promises");
const { execFile } = require("child_process");
const { ElevenLabsClient } = require("@elevenlabs/elevenlabs-js");

// George, a pre-made voice every ElevenLabs plan can use through the API.
const VOICE_ID =
  process.env.ELEVENLABS_VOICE_ID ||
  process.env.ELEVEN_VOICE_ID ||
  "JBFqnCBsd6RMkjVDRZzb";

// A voicemail is not live, so use the highest-quality, most expressive model.
const MODEL_ID = process.env.ELEVENLABS_VOICEMAIL_MODEL || "eleven_v4";

const OUTPUT_DIR = path.join(__dirname, "voicemails");

// Newest ASL -> English translation from the shared server.
async function fetchLatestAslText() {
  const baseUrl = process.env.SHARED_API_URL;
  const token = process.env.SHARED_API_TOKEN;
  if (!baseUrl || !token) {
    throw new Error("Set SHARED_API_URL and SHARED_API_TOKEN in .env.local to use --latest.");
  }

  const response = await fetch(`${baseUrl}/messages/asl?after=0`, {
    headers: { "X-API-Token": token },
  });
  if (!response.ok) {
    throw new Error(`Shared server returned ${response.status}`);
  }

  const messages = await response.json();
  const latest = messages[messages.length - 1];
  if (!latest?.clean_text?.trim()) {
    throw new Error("No ASL translations on the shared server yet.");
  }
  return latest.clean_text.trim();
}

async function makeVoicemail(text, client = new ElevenLabsClient()) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const outputPath = path.join(OUTPUT_DIR, `voicemail-${stamp}.mp3`);

  const audio = await client.textToSpeech.convert(VOICE_ID, {
    text,
    modelId: MODEL_ID,
    outputFormat: "mp3_44100_128",
  });

  // convert() returns a web ReadableStream; bridge it to a Node stream to write to disk.
  await pipeline(Readable.fromWeb(audio), fs.createWriteStream(outputPath));
  return outputPath;
}

function parseArgs(argv) {
  const play = argv.includes("--play");
  const latest = argv.includes("--latest");
  const text = argv.filter((arg) => !arg.startsWith("--")).join(" ").trim();
  return { play, latest, text };
}

async function main() {
  const { play, latest, text: argText } = parseArgs(process.argv.slice(2));
  const text = latest ? await fetchLatestAslText() : argText;

  if (!text) {
    console.log('Usage: node voicemail.js "message text" [--play]');
    console.log("       node voicemail.js --latest [--play]");
    process.exit(1);
  }
  if (!process.env.ELEVENLABS_API_KEY) {
    throw new Error("ELEVENLABS_API_KEY is missing from .env.local.");
  }

  console.log(`Making voicemail: "${text}"`);
  const outputPath = await makeVoicemail(text);
  console.log(`Saved: ${outputPath}`);
  console.log("Send it by dragging the file into a Messages conversation.");

  if (play) {
    execFile("afplay", [outputPath], (error) => {
      if (error) console.error("Could not play the file:", error.message);
    });
  }
}

module.exports = { makeVoicemail, fetchLatestAslText, parseArgs };

if (require.main === module) {
  main().catch((error) => {
    console.error("Voicemail failed:", error.message || error);
    process.exit(1);
  });
}
