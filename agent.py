            token_est > MAX_CONTEXT_TOKENS * 0.9:
                self.ui.show_activity('compacting')
                self.conversation_messages = self.context.compact_context(self.conversation_messages)
                messages = self.context.assemble_context(self.conversation_messages)
                self.ui.show_info("Context auto-compacted")

            thinking_text = ""
            response_text = ""
            tool_calls_list = []

            # BUG FIX #6: Wire thinking mode to Ollama API
            should_think = self.backend.should_think()

            try:
                self.ui.show_activity('thinking')

                stream = self.backend.stream_chat(
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    think=should_think,
                )

                parser = ThinkParser()
                self.ui.console.print()
                thinking_displayed = False

                for chunk in stream:
                    if self._interrupted:
                        break

                    msg = chunk.message if hasattr(chunk, 'message') else chunk.get('message', {}) if isinstance(chunk, dict) else None
                    if msg is None:
                        continue

                    content = ""
                    tcs = []

                    if hasattr(msg, 'content'):
                        content = msg.content or ''
                    elif isinstance(msg, dict):
                        content = msg.get('content', '') or ''

                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        tcs = list(msg.tool_calls)
                    elif isinstance(msg, dict) and msg.get('tool_calls'):
                        tcs = list(msg['tool_calls'])

                    # Handle native thinking from API
                    native_thinking = ""
                    if hasattr(msg, 'thinking') and msg.thinking:
                        native_thinking = msg.thinking

                    if native_thinking:
                        if not thinking_displayed:
                            self.ui.show_thinking_start()
                            thinking_displayed = True
                        thinking_text += native_thinking
                        sys.stdout.write(f"{ANSI['purple']}{ANSI['italic']}{native_thinking}{ANSI['reset']}")
                        sys.stdout.flush()

                    # Handle content with think tag parsing
                    if content:
                        parts = parser.feed(content)
                        for ptype, ptext in parts:
                            if ptype == 'thinking':
                                if not thinking_displayed:
                                    self.ui.show_thinking_start()
                                    thinking_displayed = True
                                thinking_text += ptext
                                sys.stdout.write(f"{ANSI['purple']}{ANSI['italic']}{ptext}{ANSI['reset']}")
                                sys.stdout.flush()
                            else:
                                response_text += ptext
                                sys.stdout.write(f"{ANSI['light_blue']}{ptext}{ANSI['reset']}")
                                sys.stdout.flush()

                    # Collect tool calls — BUG FIX #2: handle string arguments
                    for tc in tcs:
                        if hasattr(tc, 'function') and tc.function:
                            tc_name = tc.function.name if hasattr(tc.function, 'name') else tc.function.get('name', '')
                            raw_args = tc.function.arguments if hasattr(tc.function, 'arguments') else tc.function.get('arguments', {})
                            # BUG FIX #2: Parse string arguments to dict
                            if isinstance(raw_args, str):
                                try:
                                    tc_args = json.loads(raw_args)
                                except (json.JSONDecodeError, TypeError):
                                    tc_args = {}
                            elif isinstance(raw_args, dict):
                                tc_args = raw_args
                            else:
                                tc_args = {}
                            if tc_name:
                                tool_calls_list.append({'name': tc_name, 'arguments': tc_args})

                # Flush parser
                remaining = parser.flush()
                for ptype, ptext in remaining:
                    if ptype == 'thinking':
                        thinking_text += ptext
                    else:
                        response_text += ptext
                        sys.stdout.write(f"{ANSI['light_blue']}{ptext}{ANSI['reset']}")
                        sys.stdout.flush()

                sys.stdout.write('\n')
                sys.stdout.flush()

            except KeyboardInterrupt:
                self._interrupted = True
                self.ui.console.print(f"\n[{C['warning']}]⚠️ Interrupted[/{C['warning']}]")
                break
            except RuntimeError as e:
                self.ui.show_error(f"LLM call failed: {e}")
                self.ui.console.print(f"[{C['text_dim']}]Retrying in 2s...[/{C['text_dim']}]")
                time.sleep(2)
                continue
            except Exception as e:
                self.ui.show_error(f"Unexpected error: {e}")
                time.sleep(1)
                continue

            if thinking_text and not thinking_displayed:
                self.ui.show_thinking_block(thinking_text)

            # Handle tool calls
            if tool_calls_list:
                tc_serialized = [
                    {'type': 'function', 'function': {'name': tc['name'], 'arguments': tc['arguments']}}
                    for tc in tool_calls_list
                ]
                self.db.add_message(
                    self.conv_id, 'assistant',
                    content=response_text,
                    thinking=thinking_text,
                    tool_calls=json.dumps(tc_serialized),
                )
                self.conversation_messages.append({
                    'role': 'assistant',
                    'content': response_text,
                    'tool_calls': tc_serialized,
                })

                for tc in tool_calls_list:
                    tool_name = tc['name']
                    tool_args = tc['arguments'] if isinstance(tc['arguments'], dict) else {}

                    self.ui.show_tool_call(tool_name, tool_args)

                    if not self.permissions.check(tool_name, tool_args):
                        self.ui.show_permission_denied(tool_name)
                        self.db.add_message(self.conv_id, 'tool', "Permission denied by user", tool_name=tool_name)
                        self.conversation_messages.append({
                            'role': 'tool',
                            'name': tool_name,
                            'content': "Permission denied by user",
                        })
                        continue

                    # Handle ask_user specially
                    if tool_name == 'ask_user':
                        question = tool_args.get('question', '')
                        options = tool_args.get('options', [])
                        self.ui.show_ask_user(question, options)
                        try:
                            answer = input(f"{ANSI['cyan']}{MASCOT}❓ Your answer: {ANSI['reset']}").strip()
                        except (EOFError, KeyboardInterrupt):
                            answer = "(no response)"
                        result = answer
                        self.ui.console.print()
                        duration = 0.0
                    else:
                        # BUG FIX #4: Initialize duration before use
                        duration = 0.0
                        start_time = time.time()
                        result = self.tools.execute(tool_name, tool_args)
                        duration = time.time() - start_time

                    self.ui.show_tool_result(tool_name, result, duration)

                    # Truncate very long tool results for context
                    result_for_context = result
                    if len(result_for_context) > 3000:
                        result_for_context = result_for_context[:1500] + f"\n... (truncated from {len(result)} chars) ...\n" + result_for_context[-1000:]

                    self.db.add_message(self.conv_id, 'tool', result_for_context, tool_name=tool_name)
                    self.conversation_messages.append({
                        'role': 'tool',
                        'name': tool_name,
                        'content': result_for_context,
                    })

                continue  # Next turn in the loop

            # No tool calls — response is complete
            self.db.add_message(
                self.conv_id, 'assistant',
                content=response_text,
                thinking=thinking_text,
            )
            self.conversation_messages.append({
                'role': 'assistant',
                'content': response_text,
            })

            final_response = response_text
            break

        # Update conversation timestamp and auto-title
        self.db.update_conversation(self.conv_id)
        if self.db.count_messages(self.conv_id) <= 3 and user_input:
            title = user_input[:60] + ("..." if len(user_input) > 60 else "")
            self.db.update_conversation(self.conv_id, title=title)

        return final_response

    def interrupt(self):
        self._interrupted = True

    def get_context_tokens(self) -> int:
        return self.context.estimate_tokens(self.conversation_messages)


# ──────────────────────────────────────────────────────────────
# SECTION 15: AUTONOMOUS MODE
# ──────────────────────────────────────────────────────────────

class AutonomousMode:
    GUARDRAILS = {
        'max_shell_commands': 50,
        'max_rm_commands': 3,
        'max_file_writes': 20,
        'max_task_time_minutes': 120,
        'max_no_progress_minutes': 20,
        'max_llm_calls': 100,
    }

    def __init__(self, agent: AgentLoop, ui: UIRenderer, db: PincerDB, config: PincerConfig):
        self.agent = agent
        self.ui = ui
        self.db = db
        self.config = config
        self.plan = []
        self.current_step = 0
        self.stats = {'llm_calls': 0, 'commands': 0, 'writes': 0, 'errors': 0}
        self.start_time = 0
        self._cancelled = False

    def generate_plan(self, goal: str) -> list:
        self.ui.show_activity('planning')
        plan_prompt = f"""Create a detailed, step-by-step plan for this task:

{goal}

The plan should be a numbered list of specific, actionable steps.
Each step should be something that can be accomplished with file operations, shell commands, or web searches.
Be specific about what files to create or modify.
Output ONLY the numbered list, nothing else."""

        messages = self.agent.context.assemble_context(
            self.agent.conversation_messages,
            user_input=plan_prompt,
        )

        try:
            should_think = self.agent.backend.should_think()
            response = self.agent.backend.chat(messages=messages, tools=None, think=should_think)
            content = response.message.content or ''
            thinking = getattr(response.message, 'thinking', '') or ''

            if thinking:
                self.ui.show_thinking_block(thinking)

            plan = self._parse_plan(content)
            return plan
        except Exception as e:
            self.ui.show_error(f"Failed to generate plan: {e}")
            return []

    def _parse_plan(self, text: str) -> list:
        steps = []
        for line in text.splitlines():
            line = line.strip()
            match = re.match(r'^\d+[\.\)]\s+(.+)', line)
            if match:
                steps.append(match.group(1).strip())
        return steps if steps else [text[:200]]

    def execute_plan(self, goal: str, plan: list, auto_approve: bool = False):
        self.plan = plan
        self.current_step = 0
        self.start_time = time.time()
        self._cancelled = False
        self.stats = {'llm_calls': 0, 'commands': 0, 'writes': 0, 'errors': 0}

        self.ui.console.print()
        self.ui.show_plan(plan, 0)

        if not auto_approve:
            self.ui.console.print(
                f"\n  [{C['text_dim']}]\\[Enter] Start  \\[E] Edit plan  \\[Q] Cancel[/{C['text_dim']}]"
            )
            try:
                choice = input(f"\n{ANSI['cyan']}{MASCOT} > {ANSI['reset']}").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.ui.show_info("Plan cancelled.")
                return

            if choice == 'q':
                self.ui.show_info("Plan cancelled.")
                return
            elif choice == 'e':
                self.ui.show_info("Edit the plan by telling me what to change:")
                try:
                    edit_input = input(f"{ANSI['cyan']}{MASCOT} > {ANSI['reset']}").strip()
                    if edit_input:
                        original = "\n".join(f"{i+1}. {s}" for i, s in enumerate(plan))
                        modified_plan = self.generate_plan(
                            f"Modify this plan: {goal}\nOriginal plan:\n{original}\n\nChange: {edit_input}"
                        )
                        if modified_plan:
                            plan = modified_plan
                            self.plan = plan
                            self.ui.show_plan(plan, 0)
                except (EOFError, KeyboardInterrupt):
                    pass

        for i, step in enumerate(plan):
            if self._cancelled:
                self.ui.show_warning("Task cancelled by user.")
                break

            self.current_step = i
            self.ui.show_plan(plan, i)

            if not self._check_guardrails():
                break

            step_prompt = (
                f"You are executing step {i+1} of {len(plan)} in a plan to: {goal}\n\n"
                f"Current step: {step}\n\n"
                f"Execute this step now. Use the available tools to accomplish it. "
                f"After completing the step, briefly describe what you did."
            )

            try:
                self.agent.run(step_prompt)
                self.stats['llm_calls'] += 1
            except KeyboardInterrupt:
                self._cancelled = True
                self.ui.show_warning("Task interrupted.")
                break
            except Exception as e:
                self.ui.show_error(f"Step {i+1} failed: {e}")
                self.stats['errors'] += 1

            self.stats['commands'] = self.ui.commands_run
            self.stats['writes'] = self.ui.files_written
            self.stats['errors'] = self.ui.errors_count

            if (i + 1) % 5 == 0:
                self._save_checkpoint(i + 1)
                self.ui.show_info(f"Checkpoint saved at step {i+1}")

        elapsed = time.time() - self.start_time
        final_step = self.current_step + 1 if not self._cancelled else self.current_step
        self.ui.show_task_summary(self.plan, final_step, elapsed, self.stats)

    def _check_guardrails(self) -> bool:
        elapsed = time.time() - self.start_time
        if elapsed > self.GUARDRAILS['max_task_time_minutes'] * 60:
            self.ui.show_warning("Guardrail: Task exceeded maximum time.")
            return False
        if self.stats['errors'] > 10:
            self.ui.show_warning("Guardrail: Too many errors.")
            return False
        return True

    def _save_checkpoint(self, step_number: int):
        self.db.save_checkpoint(
            self.agent.conv_id,
            step_number,
            f"Step {step_number} of {len(self.plan)}",
            {
                'plan': self.plan,
                'current_step': self.current_step,
                'stats': self.stats,
                'message_count': len(self.agent.conversation_messages),
            }
        )

    def cancel(self):
        self._cancelled = True
        self.agent.interrupt()


# ──────────────────────────────────────────────────────────────
# SECTION 16: COMMAND HANDLER
# ──────────────────────────────────────────────────────────────

class CommandHandler:
    def __init__(self, app):
        self.app = app

    def handle(self, user_input: str) -> bool:
        stripped = user_input.strip()
        if not stripped.startswith('/'):
            return False

        parts = stripped.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            '/help': self._cmd_help,
            '/exit': self._cmd_exit,
            '/quit': self._cmd_exit,
            '/task': self._cmd_task,
            '/plan': self._cmd_plan,
            '/search': self._cmd_search,
            '/model': self._cmd_model,
            '/think': self._cmd_think,
            '/trust': self._cmd_trust,
            '/compact': self._cmd_compact,
            '/checkpoint': self._cmd_checkpoint,
            '/rollback': self._cmd_rollback,
            '/notes': self._cmd_notes,
            '/files': self._cmd_files,
            '/sessions': self._cmd_sessions,
            '/config': self._cmd_config,
            '/doctor': self._cmd_doctor,
            '/stats': self._cmd_stats,
            '/clear': self._cmd_clear,
            '/new': self._cmd_new,
            '/layout': self._cmd_layout,
            '/context': self._cmd_context,
            '/export': self._cmd_export,
            '/mcp': self._cmd_mcp,
        }

        handler = handlers.get(command)
        if handler:
            handler(args)
            return True

        matches = [k for k in handlers if k.startswith(command)]
        if len(matches) == 1:
            handlers[matches[0]](args)
            return True
        elif len(matches) > 1:
            self.app.ui.show_info(f"Ambiguous command. Did you mean: {', '.join(matches)}?")
            return True

        self.app.ui.show_error(f"Unknown command: {command}. Type /help for available commands.")
        return True

    def _cmd_help(self, args):
        self.app.ui.show_help()
        self.app.ui.console.print()
        self.app.ui.show_keybindings()

    def _cmd_exit(self, args):
        self.app.ui.console.print(f"\n[{C['primary_br']}]🦞 See you later! Happy coding! 👋[/{C['primary_br']}]")
        sys.exit(0)

    def _cmd_task(self, args):
        if not args.strip():
            self.app.ui.show_error("Usage: /task <goal>")
            return
        plan = self.app.autonomous.generate_plan(args.strip())
        if plan:
            auto = self.app.config.trust_level in ('auto', 'dontAsk')
            self.app.autonomous.execute_plan(goal=args.strip(), plan=plan, auto_approve=auto)

    def _cmd_plan(self, args):
        if not args.strip():
            self.app.ui.show_error("Usage: /plan <goal>")
            return
        plan = self.app.autonomous.generate_plan(args.strip())
        if plan:
            self.app.ui.show_plan(plan)

    def _cmd_search(self, args):
        if not args.strip():
            self.app.ui.show_error("Usage: /search <query>")
            return
        self.app.ui.show_activity('searching')
        result = self.app.tool_executor._tool_web_search(args.strip())
        self.app.ui._show_search_result(result)

    def _cmd_model(self, args):
        if not args.strip():
            models = self.app.backend.list_models()
            self.app.ui.console.print(f"\n[{C['accent']}]Available models:[/{C['accent']}]")
            for m in models:
                current = " ← current" if m == self.app.config.model else ""
                self.app.ui.console.print(f"  • {m}{current}")
            self.app.ui.console.print()
        else:
            try:
                self.app.backend.set_model(args.strip())
                self.app.ui.show_success(f"Model switched to {args.strip()}")
            except Exception as e:
                self.app.ui.show_error(f"Failed to switch model: {e}")

    def _cmd_think(self, args):
        mode = args.strip().lower()
        if mode not in ('on', 'off', 'auto'):
            self.app.ui.show_info(f"Current thinking mode: {self.app.config.thinking_mode}")
            self.app.ui.show_info("Usage: /think <on|off|auto>")
            return
        self.app.config.set('thinking_mode', mode)
        self.app.ui.show_success(f"Thinking mode set to: {mode}")

    def _cmd_trust(self, args):
        level = args.strip().lower()
        if level not in TRUST_LEVELS:
            self.app.ui.show_info(f"Current trust level: {self.app.config.trust_level}")
            self.app.ui.show_info(f"Available levels: {', '.join(TRUST_LEVELS)}")
            return
        self.app.config.set('trust_level', level)
        self.app.permissions.session_approvals.clear()
        self.app.ui.show_success(f"Trust level set to: {level}")

    def _cmd_layout(self, args):
        mode = args.strip().lower()
        if mode not in LAYOUT_MODES:
            self.app.ui.show_info(f"Current layout: {self.app.config.layout_mode}")
            self.app.ui.show_info(f"Available: {', '.join(LAYOUT_MODES)}")
            return
        self.app.config.set('layout_mode', mode)
        self.app.ui._layout_mode = mode
        self.app.ui.show_success(f"Layout set to: {mode}")

    def _cmd_compact(self, args):
        self.app.ui.show_activity('compacting')
        self.app.agent.conversation_messages = self.app.context.compact_context(
            self.app.agent.conversation_messages
        )
        tokens = self.app.context.estimate_tokens(self.app.agent.conversation_messages)
        self.app.ui.show_success(f"Context compacted. Estimated tokens: {tokens:,}")

    def _cmd_checkpoint(self, args):
        desc = args.strip() or "Manual checkpoint"
        self.app.db.save_checkpoint(
            self.app.agent.conv_id,
            step_number=0,
            description=desc,
            state={'messages': len(self.app.agent.conversation_messages)},
        )
        self.app.ui.show_success(f"Checkpoint saved: {desc}")

    def _cmd_rollback(self, args):
        checkpoint = self.app.db.get_latest_checkpoint(self.app.agent.conv_id)
        if not checkpoint:
            self.app.ui.show_warning("No checkpoints found.")
            return
        step, desc, state_json, created = checkpoint
        state = json.loads(state_json) if state_json else {}
        msg_count = state.get('message_count', '?')

        self.app.ui.show_info(f"Latest checkpoint: Step {step} — {desc} ({msg_count} messages)")
        self.app.ui.console.print(
            f"  [{C['warning']}]This will reset the conversation to this checkpoint.[/{C['warning']}]"
        )
        try:
            choice = input(f"{ANSI['amber']}Rollback? [y/N] {ANSI['reset']}").strip().lower()
            if choice in ('y', 'yes'):
                # Actual rollback — restore messages from DB
                target_count = msg_count if isinstance(msg_count, int) else 0
                if target_count > 0:
                    raw_msgs = self.app.db.get_messages(self.app.agent.conv_id, limit=target_count)
                    self.app.agent.conversation_messages = []
                    for role, content, thinking, tool_calls, tool_name in raw_msgs:
                        msg = {'role': role, 'content': content}
                        if thinking:
                            msg['thinking'] = thinking
                        if tool_calls:
                            try:
                                msg['tool_calls'] = json.loads(tool_calls)
                            except Exception:
                                pass
                        self.app.agent.conversation_messages.append(msg)
                    self.app.ui.show_success(f"Rolled back to checkpoint ({len(self.app.agent.conversation_messages)} messages)")
                else:
                    self.app.ui.show_warning("Cannot rollback: checkpoint has no message count")
            else:
                self.app.ui.show_info("Rollback cancelled.")
        except (EOFError, KeyboardInterrupt):
            self.app.ui.show_info("Rollback cancelled.")

    def _cmd_notes(self, args):
        if args.strip():
            results = self.app.db.search_notes(args.strip(), limit=10)
            if results:
                table = Table(
                    title=f"📝 Notes matching '{args.strip()}'",
                    border_style=C['border'], title_style=C['thinking'],
                )
                table.add_column("Category", style=C['accent'], width=12)
                table.add_column("Note", style=C['text'], width=60)
                for cat, content in results:
                    table.add_row(cat, content[:100])
                self.app.ui.console.print(table)
            else:
                self.app.ui.show_info(f"No notes matching '{args.strip()}'")
            return

        notes = self.app.db.get_notes(limit=15)
        if not notes:
            self.app.ui.show_info("No notes yet. Pinch will write notes as it learns!")
            return
        table = Table(
            title="📝 Agent Self-Notes", border_style=C['border'], title_style=C['thinking'],
        )
        table.add_column("Category", style=C['accent'], width=12)
        table.add_column("Note", style=C['text'], width=60)
        for cat, content, _ in notes:
            table.add_row(cat, content[:100])
        self.app.ui.console.print(table)

    def _cmd_files(self, args):
        self.app.ui.show_file_tree(args.strip() or None)

    def _cmd_sessions(self, args):
        sessions = self.app.db.list_conversations()
        if not sessions:
            self.app.ui.show_info("No previous sessions.")
            return
        table = Table(
            title="📂 Sessions", border_style=C['border'], title_style=C['planning'],
        )
        table.add_column("ID", style=C['accent'], width=8)
        table.add_column("Title", style=C['text'], width=40)
        table.add_column("Messages", style=C['text_dim'], width=8)
        for sid, title, created, updated in sessions:
            count = self.app.db.count_messages(sid)
            self.app.ui.console.print(f"  [{C['accent']}]{sid}[/{C['accent']}]  {title or '(untitled)'}  [{C['text_dim']}]{count} msgs[/{C['text_dim']}]")
        self.app.ui.console.print()
        self.app.ui.show_info("Use /new to start a new session, or /rollback to revert")

    def _cmd_config(self, args):
        table = Table(
            title="⚙️  Configuration", border_style=C['border'], title_style=C['accent'],
        )
        table.add_column("Setting", style=C['accent'], width=20)
        table.add_column("Value", style=C['text'], width=40)
        for key, value in sorted(self.app.config.data.items()):
            table.add_row(key, str(value))
        self.app.ui.console.print(table)

    def _cmd_doctor(self, args):
        self.app.ui.console.print(f"\n[{C['accent']}]🦞 Running diagnostics...[/{C['accent']}]")
        checks = []
        checks.append(("Python version", sys.version.split()[0], True))
        ollama_ok = self.app.backend.health_check()
        checks.append(("Ollama connection", "OK" if ollama_ok else "FAILED", ollama_ok))
        if ollama_ok:
            models = self.app.backend.list_models()
            model_ok = any(self.app.config.model in m for m in models)
            checks.append((f"Model {self.config.model}", "Available" if model_ok else "Not found", model_ok))
        db_ok = self.app.db.conn is not None
        checks.append(("Database", "OK" if db_ok else "FAILED", db_ok))
        config_ok = CONFIG_PATH.exists()
        checks.append(("Config file", "OK" if config_ok else "Missing", config_ok))
        md_ok = PINCER_MD.exists()
        checks.append(("PINCER.md", "Found" if md_ok else "Not found", md_ok))
        try:
            usage = shutil.disk_usage(str(Path.home()))
            free_gb = usage.free / (1024**3)
            checks.append(("Disk space", f"{free_gb:.1f} GB free", free_gb > 5))
        except Exception:
            checks.append(("Disk space", "Unknown", True))
        try:
            from playwright.sync_api import sync_playwright
            checks.append(("Playwright", "Installed", True))
        except ImportError:
            checks.append(("Playwright", "Not installed (optional)", True))

        table = Table(border_style=C['border'], title_style=C['accent'])
        table.add_column("Check", style=C['accent'], width=25)
        table.add_column("Status", style=C['text'], width=25)
        table.add_column("OK", width=5)
        for name, status, ok in checks:
            icon = f"[{C['success']}]✓[/{C['success']}]" if ok else f"[{C['error']}]✗[/{C['error']}]"
            table.add_row(name, status, icon)
        self.app.ui.console.print(table)
        self.app.ui.console.print()

    def _cmd_stats(self, args):
        self.app.ui.show_stats()

    def _cmd_clear(self, args):
        os.system('clear' if os.name != 'nt' else 'cls')
        self.app.ui.show_banner()

    def _cmd_new(self, args):
        title = args.strip() or ""
        self.app.agent.new_conversation(title)
        self.app.ui.show_success(f"New session started: {self.app.agent.conv_id}")

    def _cmd_context(self, args):
        messages = self.app.context.assemble_context(self.app.agent.conversation_messages)
        tokens = self.app.context.estimate_tokens(messages)
        self.app.ui.show_context_preview(messages, tokens)

    def _cmd_export(self, args):
        fmt = args.strip().lower() or 'md'
        conv_id = self.app.agent.conv_id
        raw_msgs = self.app.db.get_messages(conv_id)
        if not raw_msgs:
            self.app.ui.show_warning("No messages to export.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = Path.cwd() / "pincer_exports"
        export_dir.mkdir(exist_ok=True)

        if fmt == 'json':
            filepath = export_dir / f"pincer_session_{conv_id}_{timestamp}.json"
            export_data = []
            for role, content, thinking, tool_calls, tool_name in raw_msgs:
                export_data.append({
                    'role': role,
                    'content': content,
                    'thinking': thinking,
                    'tool_calls': tool_calls,
                    'tool_name': tool_name,
                })
            filepath.write_text(json.dumps(export_data, indent=2, ensure_ascii=False))
        else:
            filepath = export_dir / f"pincer_session_{conv_id}_{timestamp}.md"
            lines = [f"# Pincer Session Export\n", f"Date: {datetime.now().isoformat()}\n"]
            for role, content, thinking, tool_calls, tool_name in raw_msgs:
                if role == 'user':
                    lines.append(f"\n## 👤 User\n\n{content}\n")
                elif role == 'assistant':
                    if thinking:
                        lines.append(f"\n## 🦞💭 Thinking\n\n{thinking}\n")
                    lines.append(f"\n## 🦞 Assistant\n\n{content}\n")
                elif role == 'tool':
                    lines.append(f"\n## 🔧 Tool: {tool_name}\n\n```\n{content[:500]}\n```\n")
            filepath.write_text('\n'.join(lines))

        self.app.ui.show_success(f"Session exported to: {filepath}")

    def _cmd_mcp(self, args):
        parts = args.strip().split(maxsplit=1)
        if not parts or parts[0] == 'list':
            tools = self.app.mcp_client.list_available()
            if tools:
                self.app.ui.console.print(f"\n[{C['accent']}]MCP Tools:[/{C['accent']}]")
                for t in tools:
                    self.app.ui.console.print(f"  • {t}")
            else:
                self.app.ui.show_info("No MCP servers connected. Use /mcp add <name> <url>")
            return

        action = parts[0]
        if action == 'add':
            if len(parts) < 2:
                self.app.ui.show_info("Usage: /mcp add <name> <url> [auth_token]")
                return
            add_parts = parts[1].split()
            if len(add_parts) < 2:
                self.app.ui.show_info("Usage: /mcp add <name> <url> [auth_token]")
                return
            name = add_parts[0]
            url = add_parts[1]
            auth = add_parts[2] if len(add_parts) > 2 else None
            self.app.mcp_client.add_server(name, url, auth)
            tool_count = len(self.app.mcp_client.list_available())
            self.app.ui.show_success(f"MCP server '{name}' added ({tool_count} tools discovered)")
        else:
            self.app.ui.show_info("MCP commands: /mcp list, /mcp add <name> <url>")


# ──────────────────────────────────────────────────────────────
# SECTION 17: ONBOARDING
# ──────────────────────────────────────────────────────────────

class Onboarding:
    def __init__(self, config: PincerConfig, ui: UIRenderer, backend: OllamaBackend):
        self.config = config
        self.ui = ui
        self.backend = backend

    def is_first_run(self) -> bool:
        return self.config.get('first_run', True)

    def run(self):
        self.ui.console.clear()
        self.ui.console.print()
        self.ui.console.print(Panel(
            f"[{C['primary_br']}]🦞  P I N C E R  v{VERSION}[/{C['primary_br']}]\n\n"
            f"[{C['text']}]Your Autonomous Coding Companion[/{C['text']}]\n"
            f"[{C['text_dim']}]Blue Lobster Edition 🦞[/{C['text_dim']}]",
            border_style=C['primary'], padding=(2, 6),
        ))
        self.ui.console.print()

        try:
            input(f"  {ANSI['cyan']}Press Enter to begin setup...{ANSI['reset']}")
        except (EOFError, KeyboardInterrupt):
            pass

        # Step 1: Environment
        self.ui.console.print(f"\n[{C['accent']}]Step 1/4: Environment Check[/{C['accent']}]")
        checks = []
        checks.append(("Python", sys.version.split()[0], True))
        ollama_ok = self.backend.health_check()
        checks.append(("Ollama", "Running" if ollama_ok else "Not running!", ollama_ok))
        if ollama_ok:
            models = self.backend.list_models()
            model_ok = any(self.config.model in m for m in models)
            checks.append((f"Model {self.config.model}", "Available" if model_ok else "Not pulled", model_ok))
            if not model_ok:
                self.ui.console.print(f"  [{C['warning']}]Pulling {self.config.model}...[/{C['warning']}]")
                try:
                    self.backend.client.pull(self.config.model)
                    self.ui.console.print(f"  [{C['success']}]✓ Pulled![/{C['success']}]")
                except Exception:
                    self.ui.console.print(f"  [{C['error']}]Failed. Run: ollama pull {self.config.model}[/{C['error']}]")

        for name, status, ok in checks:
            icon = "✓" if ok else "✗"
            color = C['success'] if ok else C['error']
            self.ui.console.print(f"  [{color}]{icon}[/{color}] {name}: {status}")

        # Step 2: Personalization
        self.ui.console.print(f"\n[{C['accent']}]Step 2/4: Personalization[/{C['accent']}]")
        try:
            name = input(f"  Your name? [{self.config.user_name}]: ").strip()
            if name:
                # BUG FIX #5: Store name safely (no HTML injection risk since we use FormattedText)
                self.config.set('user_name', name)
                self.ui.user_name = name
        except (EOFError, KeyboardInterrupt):
            pass

        try:
            self.ui.console.print("  Preferred language:")
            self.ui.console.print("  [1] Python  [2] JavaScript  [3] Rust  [4] Go  [5] TypeScript  [6] Other")
            lang_choice = input("  Choice [1]: ").strip()
            lang_map = {'1': 'python', '2': 'javascript', '3': 'rust', '4': 'go', '5': 'typescript', '6': 'other'}
            lang = lang_map.get(lang_choice, 'python')
            self.config.set('language', lang)
        except (EOFError, KeyboardInterrupt):
            lang = 'python'

        try:
            self.ui.console.print("  Experience level:")
            self.ui.console.print("  [1] Beginner  [2] Intermediate  [3] Advanced")
            exp_choice = input("  Choice [2]: ").strip()
            exp_map = {'1': 'Beginner', '2': 'Intermediate', '3': 'Advanced'}
            self.config.set('experience', exp_map.get(exp_choice, 'Intermediate'))
        except (EOFError, KeyboardInterrupt):
            pass

        # Step 3: Trust
        self.ui.console.print(f"\n[{C['accent']}]Step 3/4: Trust Level[/{C['accent']}]")
        self.ui.console.print("  How much should Pinch ask before acting?")
        self.ui.console.print(f"  [{C['text_dim']}][1] 🔒 plan       — Approve everything first[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text']}]   [2] 🔒 default    — Ask before shell commands (recommended)[/{C['text']}]")
        self.ui.console.print(f"  [{C['text_dim']}][3] 🔓 acceptEdits — Auto-approve file edits[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}][4] 🔓 auto       — Auto-approve safe commands[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}][5] 🔓 dontAsk    — Skip most prompts[/{C['text_dim']}]")
        try:
            trust_choice = input("  Choice [2]: ").strip()
            trust_map = {'1': 'plan', '2': 'default', '3': 'acceptEdits', '4': 'auto', '5': 'dontAsk'}
            self.config.set('trust_level', trust_map.get(trust_choice, 'default'))
        except (EOFError, KeyboardInterrupt):
            self.config.set('trust_level', 'default')

        # Step 4: Complete
        self.ui.console.print(f"\n[{C['accent']}]Step 4/4: All Set! 🎉[/{C['accent']}]")
        self.ui.console.print(f"  [{C['success']}]✓[/{C['success']}] Environment ready")
        self.ui.console.print(f"  [{C['success']}]✓[/{C['success']}] Personalization saved")
        self.ui.console.print(f"  [{C['success']}]✓[/{C['success']}] Trust level: {self.config.trust_level}")
        self.ui.console.print(f"  [{C['success']}]✓[/{C['success']}] Theme: Deep Ocean (blue)")
        self.ui.console.print()
        self.ui.console.print(f"  [{C['text_dim']}]Quick tips:[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}]• Type /help for all commands[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}]• Use /task for autonomous mode[/{C['text_dim']}]")
        self.ui.console.print(f"  [{C['text_dim']}]• Pinch 🦞 will always show you what it's doing[/{C['text_dim']}]")
        self.ui.console.print()

        self.config.set('first_run', False)
        self.config.save()

        try:
            input(f"  {ANSI['cyan']}Press Enter to start Pincer...{ANSI['reset']}")
        except (EOFError, KeyboardInterrupt):
            pass


# ──────────────────────────────────────────────────────────────
# SECTION 18: MAIN APPLICATION
# ──────────────────────────────────────────────────────────────

class PincerApp:
    def __init__(self):
        self.config = PincerConfig()
        self.db = PincerDB()
        self.backend = OllamaBackend(self.config)
        self.ui = UIRenderer(self.config)
        self.memory = MemorySystem(self.db, self.config)
        self.tool_executor = ToolExecutor(self.config, self.db)
        self.permissions = PermissionSystem(self.config, self.ui.console)
        self.context = ContextManager(self.config, self.memory, self.backend)
        self.mcp_client = MCPClient()
        self.agent = AgentLoop(
            self.config, self.db, self.backend,
            self.tool_executor, self.permissions, self.context, self.memory, self.ui
        )
        self.autonomous = AutonomousMode(self.agent, self.ui, self.db, self.config)
        self.commands = CommandHandler(self)
        self.onboarding = Onboarding(self.config, self.ui, self.backend)

        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

        slash_commands = [
            '/help', '/exit', '/quit', '/task', '/plan', '/search', '/model',
            '/think', '/trust', '/compact', '/checkpoint', '/rollback',
            '/notes', '/files', '/sessions', '/config', '/doctor',
            '/stats', '/clear', '/new', '/layout', '/context', '/export', '/mcp',
        ]
        self.prompt_session = PromptSession(
            history=FileHistory(str(HISTORY_PATH)),
            completer=FuzzyWordCompleter(slash_commands, ignore_case=True),
            multiline=False,
        )

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, sig, frame):
        self.agent.interrupt()
        self.autonomous.cancel()

    def _build_prompt(self) -> FormattedText:
        """BUG FIX #5: Use FormattedText instead of raw HTML to prevent injection."""
        safe_name = self.config.user_name.replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')
        return FormattedText([
            (f'fg:{C["primary_br"]}', f'{MASCOT} '),
            (f'fg:{C["text_dim"]}', f'{safe_name}'),
            (f'fg:{C["accent"]}', ' > '),
        ])

    def run(self):
        if self.onboarding.is_first_run():
            self.onboarding.run()

        os.system('clear' if os.name != 'nt' else 'cls')
        self.ui.show_banner()
        self.ui.show_welcome()

        self.agent.new_conversation()

        ctx_tokens = self.agent.get_context_tokens()
        self.ui.show_status_bar('idle', ctx_tokens)
        self.ui.console.print(
            f"[{C['text_dim']}]Model: {self.config.model} | Trust: {self.config.trust_level} | "
            f"Think: {self.config.thinking_mode} | Layout: {self.config.layout_mode}[/{C['text_dim']}]"
        )
        self.ui.console.print()

        while True:
            try:
                prompt_formatted = self._build_prompt()
                user_input = self.prompt_session.prompt(prompt_formatted)

                if not user_input.strip():
                    continue

                if self.commands.handle(user_input):
                    continue

                self.ui.show_status_bar('thinking', self.agent.get_context_tokens())
                result = self.agent.run(user_input.strip())
                ctx_tokens = self.agent.get_context_tokens()
                self.ui.show_status_bar('idle' if result else 'error', ctx_tokens)

            except KeyboardInterrupt:
                self.ui.console.print(f"\n[{C['text_dim']}]Use /exit to quit.[/{C['text_dim']}]")
                continue
            except EOFError:
                self.ui.console.print(f"\n[{C['primary_br']}]🦞 See you later! 👋[/{C['primary_br']}]")
                break
            except Exception as e:
                self.ui.show_error(f"Unexpected error: {e}")
                if self.config.get('experience') == 'Advanced':
                    import traceback
                    self.ui.console.print(f"[{C['text_muted']}]{traceback.format_exc()}[/{C['text_muted']}]")
                continue

        self.db.close()


# ──────────────────────────────────────────────────────────────
# SECTION 19: ENTRY POINT
# ──────────────────────────────────────────────────────────────

def main():
    PINCER_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        client = ollama.Client(host='http://localhost:11434')
        client.list()
    except Exception:
        print(f"\n{ANSI['red']}{MASCOT} Cannot connect to Ollama!{ANSI['reset']}")
        print(f"{ANSI['amber']}  Make sure Ollama is running:{ANSI['reset']}")
        print(f"{ANSI['amber']}    ollama serve{ANSI['reset']}")
        print()
        print(f"{ANSI['dim_blue']}  Install Ollama: https://ollama.com{ANSI['reset']}")
        print()
        sys.exit(1)

    app = PincerApp()
    app.run()


if __name__ == "__main__":
    main()
