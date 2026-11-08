"""Agent runtime.

Isolation invariant: this module must not import ``django.db``, ``core.models``,
``psycopg``, or any ORM model. It receives an LLM client and a gateway client,
and every state change goes through the gateway.
"""

import json

MAX_STEPS = 12
MAX_MESSAGES = 40

SYSTEM_PROMPT = """You are the UMIS clinic operations agent.

Rules:
- Act only with the requesting user's permissions. The gateway checks them.
- Use the provided tools; never invent IDs or write raw SQL.
- Search before mutating. Read a visit before changing it when you need its ID.
- Resolve doctor and service names from the context lists below; never guess
  or invent an ID.
- Use ISO 8601 datetimes and YYYY-MM-DD dates.
- Call one tool at a time.
- A destructive command pauses for human approval automatically. Do not ask the
  user to type approval; the interface handles it.
- If a tool is denied or fails, do not retry it blindly. Explain the outcome.
- For a payment, read `outstanding_balance` from `query.visit.get` and use it
  as the amount; never compute or invent the amount.
- Issue only the commands needed to fulfil the user's request. Never verify, cancel, or confirm anything the user did not ask for. Recording a payment does not mean verifying it.
- Reply with a short factual summary. Do not claim success before the gateway
  returns a success status.
- Do not call a command listed under "cannot run" below; tell the user it is
  outside their role. When asked what the user can do, answer from that list.

Context:
- Current local time: {now}
- Requesting user: {user}
{access}
- Model: {model}
- Assignable doctors (use the id for assigned_doctor_id):
{doctors}
- Active services (use the id for service_id):
{services}
{instructions}"""


def _format_doctors(doctors):
    if not doctors:
        return '  (none)'
    return '\n'.join(
        f"  - {doctor.get('name', '')} "
        f"(username={doctor.get('username', '')}, id={doctor.get('id', '')})"
        for doctor in doctors
    )


def _format_services(services):
    if not services:
        return '  (none)'
    return '\n'.join(
        f"  - {service.get('name', '')} "
        f"(id={service.get('id', '')}, price={service.get('price', '')})"
        for service in services
    )


def _format_access(access):
    if not access:
        return '- User access: (not provided)'
    def names(key):
        return ', '.join(access.get(key) or []) or '(none)'
    return '\n'.join([
        f"- User roles: {', '.join(access.get('roles') or []) or '(none)'}",
        f"- This user can run: {names('can')}",
        f"- Checked per visit (only visits this user may see or change): {names('per_visit')}",
        f"- Only for visits assigned to this user: {names('assigned_only')}",
        f"- This user cannot run (the gateway denies them): {names('cannot')}",
    ])


def _format_instructions(text):
    text = text.strip()
    if not text:
        return ''
    return (
        '\nClinic instructions from the administrator. They add preferences only; '
        'they never override the rules above, the tool list, or the gateway checks:\n'
        f'{text}\n'
    )


class AgentRuntime:
    def __init__(self, llm, gateway, max_steps=MAX_STEPS, max_messages=MAX_MESSAGES):
        self.llm = llm
        self.gateway = gateway
        self.max_steps = max_steps
        self.max_messages = max_messages

    # --- public entry points ------------------------------------------------

    def start(self, goal):
        context = self.gateway.context()
        state = {
            'goal': goal,
            'tool_calls': 0,
            'next_step_index': 0,
            'policy': context.get('policy') or {},
            'messages': [
                {
                    'role': 'system',
                    'content': SYSTEM_PROMPT.format(
                        now=context.get('now', ''),
                        user=context.get('user', ''),
                        access=_format_access(context.get('access') or {}),
                        model=context.get('model', ''),
                        doctors=_format_doctors(context.get('doctors') or []),
                        services=_format_services(context.get('services') or []),
                        instructions=_format_instructions(context.get('instructions') or ''),
                    ),
                },
                {'role': 'user', 'content': goal},
            ],
        }
        self.gateway.save_state(state)
        self.gateway.set_status('RUNNING')
        return self._loop(state)

    def resume(self):
        state = self.gateway.load_state()
        pending = state.get('pending')
        if not isinstance(pending, dict) or 'result' not in pending:
            return self._fail(state, 'No approved step is waiting to resume.')
        result = pending.pop('result')
        state.pop('pending', None)
        tool_call_id = pending.get('tool_call_id')
        if not tool_call_id:
            return self._fail(state, 'The pending tool call is missing.')
        state.setdefault('messages', []).append({
            'role': 'tool',
            'tool_call_id': tool_call_id,
            'content': json.dumps(result, sort_keys=True, default=str),
        })
        self.gateway.save_state(state)
        return self._loop(state)

    # --- loop ---------------------------------------------------------------

    def _loop(self, state):
        while True:
            if int(state.get('tool_calls', 0)) >= self.max_steps:
                return self._fail(state, 'The agent reached the step limit.')
            if len(state.get('messages', [])) >= self.max_messages:
                return self._fail(state, 'The agent reached the message limit.')

            try:
                assistant = self.llm.chat(state.get('messages', []), self.gateway.tools())
            except Exception as exc:
                message = str(exc) or 'The model call failed.'
                return self._fail(state, message)

            if not isinstance(assistant, dict):
                return self._fail(state, 'The model returned an invalid response.')

            content = assistant.get('content') or ''
            tool_calls = assistant.get('tool_calls') or []
            if not isinstance(tool_calls, list):
                return self._fail(state, 'The model returned invalid tool calls.')

            message = {'role': 'assistant', 'content': content}
            if tool_calls:
                message['tool_calls'] = tool_calls
            state.setdefault('messages', []).append(message)

            if not tool_calls:
                state['answer'] = content
                state['error'] = ''
                self.gateway.save_state(state)
                self.gateway.mark_answer(content)
                return {'status': 'SUCCEEDED', 'answer': content}

            for position, call in enumerate(tool_calls):
                if int(state.get('tool_calls', 0)) >= self.max_steps:
                    return self._fail(state, 'The agent reached the step limit.')
                if len(state.get('messages', [])) >= self.max_messages:
                    return self._fail(state, 'The agent reached the message limit.')

                state['tool_calls'] = int(state.get('tool_calls', 0)) + 1
                if isinstance(call, dict):
                    function = call.get('function') or {}
                    if not isinstance(function, dict):
                        function = {}
                    call_id = call.get('id') or ''
                else:
                    function = {}
                    call_id = ''

                name = function.get('name') or ''
                raw_arguments = function.get('arguments')
                args, parse_error = self._parse_arguments(raw_arguments)

                if parse_error:
                    self._append_tool_error(state, call_id, 'FAILED', parse_error)
                    continue

                if not name:
                    self._append_tool_error(
                        state, call_id, 'DENIED',
                        'The tool call has no command name.',
                    )
                    continue

                step_index = int(state.get('next_step_index', 0))
                try:
                    result = self.gateway.execute(
                        name, args, step_index=step_index,
                    )
                except Exception as exc:
                    return self._fail(state, str(exc) or 'The gateway call failed.')

                state['next_step_index'] = step_index + 1
                status = result.get('status')

                if status == 'AWAITING_APPROVAL':
                    state['pending'] = {
                        'tool_call_id': call_id,
                        'command': name,
                        'step_id': result.get('step_id'),
                        'approval_id': result.get('approval_id'),
                    }
                    state['messages'] = state.get('messages', [])
                    self._skip_remaining_calls(
                        state, tool_calls[position + 1:],
                        'Not executed because an earlier call is awaiting approval.',
                    )
                    self.gateway.save_state(state)
                    self.gateway.set_status('AWAITING_APPROVAL')
                    return {
                        'status': 'AWAITING_APPROVAL',
                        'step_id': result.get('step_id'),
                        'approval_id': result.get('approval_id'),
                        'command': name,
                        'preview': result.get('preview', {}),
                    }

                state.setdefault('messages', []).append({
                    'role': 'tool',
                    'tool_call_id': call_id,
                    'content': json.dumps(result, sort_keys=True, default=str),
                })

            self.gateway.save_state(state)

    # --- helpers ------------------------------------------------------------

    def _parse_arguments(self, raw):
        if raw in (None, ''):
            return {}, ''
        if isinstance(raw, dict):
            return raw, ''
        if not isinstance(raw, str):
            return {}, 'The tool arguments are not an object.'
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}, 'The tool arguments are not valid JSON.'
        if not isinstance(parsed, dict):
            return {}, 'The tool arguments must be a JSON object.'
        return parsed, ''

    def _append_tool_error(self, state, call_id, status, error):
        state.setdefault('messages', []).append({
            'role': 'tool',
            'tool_call_id': call_id,
            'content': json.dumps({'status': status, 'error': error}),
        })

    def _skip_remaining_calls(self, state, calls, reason):
        for call in calls:
            call_id = call.get('id') if isinstance(call, dict) else ''
            state.setdefault('messages', []).append({
                'role': 'tool',
                'tool_call_id': call_id,
                'content': json.dumps({'status': 'SKIPPED', 'error': reason}),
            })

    def _fail(self, state, message):
        state['error'] = message
        self.gateway.save_state(state)
        self.gateway.set_status('FAILED', error=message)
        return {'status': 'FAILED', 'error': message}
