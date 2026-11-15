"""Existing Chert/LiveKit connectivity test; webcam translation is separate."""
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentServer, JobContext, AutoSubscribe, cli

load_dotenv(Path(__file__).with_name('.env'))
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('asl-agent')
server = AgentServer()


async def read_video(track):
    stream = rtc.VideoStream(track)
    try:
        count = 0
        async for event in stream:
            count += 1
            if count % 30 == 0:
                log.info('Received %s frames (%sx%s)', count, event.frame.width, event.frame.height)
    finally:
        await stream.aclose()


@server.rtc_session(agent_name='asl-agent')
async def entrypoint(ctx: JobContext):
    tasks = {}

    @ctx.room.on('track_subscribed')
    def on_track(track, publication, participant):
        log.info('Track %s from %s', publication.name, participant.identity)
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            tasks[publication.sid] = asyncio.create_task(read_video(track))

    @ctx.room.on('track_unsubscribed')
    def on_unsubscribe(track, publication, participant):
        task = tasks.pop(publication.sid, None)
        if task:
            task.cancel()

    async def cleanup():
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    ctx.add_shutdown_callback(cleanup)
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    log.info('Connected to %s; waiting for video', ctx.room.name)


if __name__ == '__main__':
    cli.run_app(server)
