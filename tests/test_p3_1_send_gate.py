"""P3.1 send gate: reproduce two coroutines re-entering one thread RLock.
Only temporary synthetic control rows and an injected recall are used.
This is concurrency evidence, not a Kernel or real-client acceptance test.
"""
import asyncio
import json
import platform
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p2_3_delivery import _insert, _grant, _events
from zhiwo.api.channel import handoff
from zhiwo.repositories.migrate import migrate
from zhiwo.services.agent_tools import search_memory
from zhiwo.services.agents import create_agent, update_agent

async def probe(db, ids, agent):
    entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = asyncio.Event()
    messages = []
    async def first_send(message):
        if message['type'] == 'http.response.start':
            entered.set()
            await release.wait()
    async def second_send(message):
        second_entered.set()
        messages.append(message)
    first = asyncio.create_task(handoff(db, ids[0], first_send))
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(handoff(db, ids[1], second_send))
    await asyncio.sleep(.15)
    reentered = second_entered.is_set()
    mutation = asyncio.create_task(asyncio.to_thread(update_agent, db, agent['id'], str(uuid.uuid4()), {'enabled': False}))
    await asyncio.sleep(.15)
    waited = not mutation.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second, mutation), 10)
    after = _events(db)
    body = json.loads(messages[-1]['body'])
    return {'same_event_loop_reentered_lock': reentered, 'revocation_waited_for_send': waited,
            'snapshot_equals_second_response': json.loads(after[1]['response_snapshot']) == body,
            'both_delivered': all(row['delivery_state'] == 'sent' for row in after)}

with tempfile.TemporaryDirectory(prefix='zhiwo-p31-lock-') as raw:
    db = Path(raw) / 'zhiwo.db'
    migrate(db)
    agent = create_agent(db, str(uuid.uuid4()), '合成并发复核连接')
    _grant(db, agent['id'])
    _insert(db, '合成并发句子。', 'kernel-synthetic')
    for _ in range(2):
        search_memory(db, None, agent['credential'], '合成并发句子。', recall=lambda *_: [{'id':'kernel-synthetic','content':'合成并发句子。'}])
    result = asyncio.run(probe(db, [row['id'] for row in _events(db)], agent))
    result.update(task='P3.1-send-lock', platform=platform.platform(), synthetic_recall=True)
    result['pass'] = not result['same_event_loop_reentered_lock'] and result['revocation_waited_for_send'] and result['snapshot_equals_second_response'] and result['both_delivered']
    output = Path(__file__).parent / 'results' / ('p3_1_lock_before.json' if '--baseline' in sys.argv else 'p3_1_lock_windows.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    if '--baseline' not in sys.argv:
        assert result['pass'], 'send sections must be mutually exclusive across coroutines'
