"""
Telegram Bot for Orderbook Daemon control, historical trade job management,
Google Drive upload scheduling, and monitoring notifications.
"""
import asyncio
import os
import threading
import logging
import uuid
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, CommandHandler, CallbackQueryHandler,
        ContextTypes, MessageHandler, filters
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed. Install with: pip install python-telegram-bot>=21.0")


class TelegramBot:
    """Telegram bot for remote control of the Orderbook Daemon,
    historical trade jobs, and Google Drive uploads."""

    _INSTANCE_MARKER = Path('config_data/telegram_bot_instance.txt')

    def __init__(self, token: str, chat_id: str, daemon,
                 heartbeat_minutes: int = 15,
                 job_manager=None,
                 gdrive_uploader=None,
                 orderbook_dir: Optional[Path] = None):
        if not TELEGRAM_AVAILABLE:
            raise ImportError("python-telegram-bot is not installed")

        self.token = token
        self.chat_id = chat_id
        self.daemon = daemon
        self.heartbeat_minutes = heartbeat_minutes
        self.job_manager = job_manager
        self.gdrive_uploader = gdrive_uploader
        self.orderbook_dir = orderbook_dir

        self.app = None
        self.thread = None
        self.running = False
        self._loop = None
        self._last_error = None
        self._bot_info = None
        self._job_queue_available = False
        self._recap_sent_date = None

        self._instance_id = f"{os.getpid()}_{uuid.uuid4().hex[:8]}"

        self.daemon.set_reconnect_callback(self._on_reconnect_alert)
        self.daemon.set_state_change_callback(self._on_state_change)

        # register ourselves as the notification sink for historical jobs
        if self.job_manager:
            self.job_manager.set_notification_callback(self._on_job_event)

    # ------------------------------------------------------------------ #
    #  Instance marker (zombie-bot guard)
    # ------------------------------------------------------------------ #

    def _claim_active_instance(self):
        try:
            self._INSTANCE_MARKER.parent.mkdir(parents=True, exist_ok=True)
            self._INSTANCE_MARKER.write_text(self._instance_id)
            logger.info(f"Claimed active bot instance: {self._instance_id}")
        except Exception as e:
            logger.warning(f"Could not write instance marker: {e}")

    def _is_active_instance(self) -> bool:
        try:
            return self._INSTANCE_MARKER.read_text().strip() == self._instance_id
        except Exception:
            return True

    # ------------------------------------------------------------------ #
    #  Build app & schedule jobs
    # ------------------------------------------------------------------ #

    def _build_app(self):
        builder = Application.builder().token(self.token)
        self.app = builder.build()

        # -- orderbook daemon commands --
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("tickers", self._cmd_tickers))
        self.app.add_handler(CommandHandler("addticker", self._cmd_add_ticker))
        self.app.add_handler(CommandHandler("removeticker", self._cmd_remove_ticker))
        self.app.add_handler(CommandHandler("settickers", self._cmd_set_tickers))
        self.app.add_handler(CommandHandler("pause", self._cmd_pause))
        self.app.add_handler(CommandHandler("resume", self._cmd_resume))
        self.app.add_handler(CommandHandler("settoken", self._cmd_set_token))
        self.app.add_handler(CommandHandler("recap", self._cmd_recap))
        self.app.add_handler(CommandHandler("heartbeat", self._cmd_heartbeat))
        self.app.add_handler(CommandHandler("setheartbeat", self._cmd_set_heartbeat))

        # -- historical trade job commands --
        self.app.add_handler(CommandHandler("jobs", self._cmd_jobs))
        self.app.add_handler(CommandHandler("newjob", self._cmd_new_job))
        self.app.add_handler(CommandHandler("jobstatus", self._cmd_job_status))
        self.app.add_handler(CommandHandler("pausejob", self._cmd_pause_job))
        self.app.add_handler(CommandHandler("resumejob", self._cmd_resume_job))
        self.app.add_handler(CommandHandler("canceljob", self._cmd_cancel_job))

        # inline keyboard handler
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

    def _schedule_jobs(self):
        job_queue = self.app.job_queue

        if job_queue is None:
            self._job_queue_available = False
            logger.warning(
                "JobQueue is not available. Scheduled jobs will NOT run. "
                "Install with: pip install \"python-telegram-bot[job-queue]\""
            )
            return

        self._job_queue_available = True

        # heartbeat
        if self.heartbeat_minutes > 0:
            job_queue.run_repeating(
                self._job_heartbeat,
                interval=self.heartbeat_minutes * 60,
                first=60,
                name="heartbeat"
            )
            logger.info(f"Heartbeat scheduled every {self.heartbeat_minutes} min")

        # token reminder: 07:30 WIB = 00:30 UTC
        job_queue.run_daily(
            self._job_token_reminder,
            time=dt_time(hour=0, minute=30),
            name="token_reminder"
        )
        logger.info("Token reminder scheduled at 07:30 WIB")

        # pre-market: 08:25 WIB = 01:25 UTC
        job_queue.run_daily(
            self._job_pre_market,
            time=dt_time(hour=1, minute=25),
            name="pre_market"
        )

        # daily recap: 16:30 WIB = 09:30 UTC
        job_queue.run_daily(
            self._job_daily_recap,
            time=dt_time(hour=9, minute=30),
            name="daily_recap"
        )

        # Google Drive post-market upload: 16:15 WIB = 09:15 UTC
        job_queue.run_daily(
            self._job_gdrive_post_market,
            time=dt_time(hour=9, minute=15),
            name="gdrive_post_market"
        )

        # Google Drive midnight catch-all: 00:05 WIB = 17:05 UTC (previous day)
        job_queue.run_daily(
            self._job_gdrive_midnight,
            time=dt_time(hour=17, minute=5),
            name="gdrive_midnight"
        )

        logger.info("All scheduled jobs registered")

    # ------------------------------------------------------------------ #
    #  Bot lifecycle
    # ------------------------------------------------------------------ #

    def start(self):
        if self.running:
            return

        self._claim_active_instance()
        self._build_app()
        self._schedule_jobs()
        self.running = True

        def run_bot():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                loop.run_until_complete(self._run_polling())
            except Exception as e:
                self._last_error = str(e)
                logger.error(f"Telegram bot error: {e}", exc_info=True)
            finally:
                self.running = False
                self._loop = None

        self.thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
        self.thread.start()
        logger.info("Telegram bot started in background thread")

    async def _run_polling(self):
        await self.app.initialize()

        try:
            me = await self.app.bot.get_me()
            self._bot_info = {
                'id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'is_bot': me.is_bot,
            }
            logger.info(f"Telegram bot validated: @{me.username} (ID: {me.id})")
        except Exception as e:
            self._last_error = f"Token validation failed: {e}"
            logger.error(f"Telegram bot token validation failed: {e}")
            raise

        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)

        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text="*Orderbook Bot Online*\n\nDaemon is running. Use /help for commands.",
                parse_mode="Markdown"
            )
        except Exception as e:
            self._last_error = f"Failed to send startup message: {e}"
            logger.error(f"Failed to send startup message: {e}")

        while self.running:
            await asyncio.sleep(1)

        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    def stop(self):
        self.running = False
        logger.info("Telegram bot stopping")

    def get_status(self):
        return {
            'running': self.running,
            'token_configured': bool(self.token),
            'token_masked': f"{self.token[:8]}...{self.token[-4:]}" if self.token and len(self.token) > 12 else '***',
            'chat_id_configured': bool(self.chat_id),
            'chat_id': self.chat_id,
            'bot_info': self._bot_info,
            'job_queue_available': self._job_queue_available,
            'heartbeat_minutes': self.heartbeat_minutes,
            'last_error': self._last_error,
        }

    # ---- thread-safe message helper ----

    def _send_async(self, coro):
        """Schedule a coroutine on the bot's event loop from any thread."""
        if not self.running or not self._loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as e:
            logger.error(f"Failed to schedule async send: {e}")

    async def _async_send_test_message(self):
        if not self.app:
            return {'success': False, 'error': 'Bot not initialized'}
        try:
            bot_name = self._bot_info.get('username', 'Unknown') if self._bot_info else 'Unknown'
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    f"*Test Message*\n\n"
                    f"Bot @{bot_name} is connected!\n"
                    f"Chat ID: `{self.chat_id}`\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                ),
                parse_mode="Markdown"
            )
            return {'success': True, 'message': f'Test message sent to {self.chat_id}'}
        except Exception as e:
            self._last_error = f"Test message failed: {e}"
            return {'success': False, 'error': str(e)}

    def send_test_message(self):
        if not self.running or not self._loop:
            return {'success': False, 'error': 'Bot is not running'}
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._async_send_test_message(), self._loop
            )
            return future.result(timeout=10)
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ================================================================== #
    #  COMMAND HANDLERS — Orderbook Daemon
    # ================================================================== #

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        await update.message.reply_text(
            f"*Orderbook Streaming Bot*\n\n"
            f"Your Chat ID: `{chat_id}`\n\n"
            f"Use /help to see available commands.\n"
            f"Use /status for current daemon status.\n",
            parse_mode="Markdown"
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "*Available Commands*\n\n"
            "*Status & Info*\n"
            "/status - Daemon & market status\n"
            "/heartbeat - Connection health check\n"
            "/recap - Today's trading recap\n\n"
            "*Ticker Management*\n"
            "/tickers - View current tickers\n"
            "/settickers BBCA TLKM - Replace all\n"
            "/addticker BBCA - Add ticker(s)\n"
            "/removeticker BBCA - Remove ticker(s)\n\n"
            "*Stream Control*\n"
            "/pause - Pause streaming\n"
            "/resume - Resume streaming\n\n"
            "*Authentication*\n"
            "/settoken <token> - Set bearer token\n\n"
            "*Historical Trade Jobs*\n"
            "/jobs - List recent jobs\n"
            "/newjob BBCA,TLKM 2026-01-01 2026-01-31 - Create job\n"
            "/jobstatus <id> - Job details\n"
            "/pausejob <id> - Pause a job\n"
            "/resumejob <id> - Resume a job\n"
            "/canceljob <id> - Cancel a job\n\n"
            "*Settings*\n"
            f"/setheartbeat <min> - Change heartbeat interval (now {self.heartbeat_minutes}m)\n\n"
            "*Scheduled*\n"
            f"  Heartbeat: every {self.heartbeat_minutes} min\n"
            "  Token reminder: 07:30 WIB\n"
            "  Pre-market: 08:25 WIB\n"
            "  Daily recap: 16:30 WIB\n"
            "  GDrive upload: 16:15 WIB + 00:05 WIB\n"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status = self.daemon.get_status()
        state_emojis = {
            'streaming': '🟢', 'waiting_market': '🟡', 'market_closed': '🌙',
            'paused': '⏸️', 'error': '🔴', 'no_tickers': '📋',
        }
        emoji = state_emojis.get(status['state'], '❓')
        market = status.get('market', {})
        market_emoji = '🟢' if market.get('is_open') else '🔴'

        text = (
            f"{emoji} *Daemon: {status['state'].replace('_', ' ').title()}*\n"
            f"{market_emoji} Market: {market.get('reason', 'Unknown')}\n"
            f"WIB: {market.get('current_time', '?')[:19].replace('T', ' ')}\n\n"
        )

        tickers = status.get('tickers', [])
        text += f"*Tickers ({len(tickers)}):* {', '.join(tickers) if tickers else 'None'}\n"

        if status.get('stream'):
            stream = status['stream']
            msg_counts = stream.get('message_counts', {})
            total = sum(msg_counts.values())
            text += (
                f"\n*Stream*\n"
                f"  Messages: {total:,}\n"
                f"  Reconnects: {stream.get('total_reconnects', 0)}\n"
                f"  Uptime: {self._format_uptime(stream.get('uptime_seconds', 0))}\n"
            )
            if msg_counts:
                text += "\n*Per-Ticker:*\n"
                for ticker, count in sorted(msg_counts.items(), key=lambda x: x[1], reverse=True):
                    text += f"  `{ticker}`: {count:,}\n"

        if market.get('time_until_next', 0) > 0:
            text += f"\nNext open in: {self._format_uptime(market['time_until_next'])}"

        keyboard = []
        if status['state'] == 'streaming':
            keyboard.append([InlineKeyboardButton("⏸ Pause", callback_data="pause")])
        elif status.get('paused'):
            keyboard.append([InlineKeyboardButton("▶️ Resume", callback_data="resume")])
        keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh_status")])

        await update.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )

    async def _cmd_tickers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status = self.daemon.get_status()
        tickers = status.get('tickers', [])
        if not tickers:
            await update.message.reply_text("No tickers configured.\n\nUse /settickers BBCA TLKM to set tickers.")
            return
        text = f"*Active Tickers ({len(tickers)})*\n\n"
        for t in tickers:
            text += f"  `{t}`\n"
        keyboard = [[InlineKeyboardButton("Replace All", callback_data="prompt_set_tickers")]]
        await update.message.reply_text(text, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(keyboard))

    async def _cmd_set_tickers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /settickers BBCA TLKM BMRI ...")
            return
        result = self.daemon.set_tickers(context.args)
        tickers = result.get('tickers', [])
        old = result.get('old_tickers', [])
        await update.message.reply_text(
            f"*Tickers Updated*\n\nOld: {', '.join(old) if old else 'None'}\nNew: {', '.join(tickers)}\n",
            parse_mode="Markdown"
        )

    async def _cmd_add_ticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /addticker BBCA TLKM ...")
            return
        result = self.daemon.add_tickers(context.args)
        added = result.get('added', [])
        all_tickers = result.get('tickers', [])
        if added:
            await update.message.reply_text(f"Added: {', '.join(added)}\nAll tickers: {', '.join(all_tickers)}")
        else:
            await update.message.reply_text("All specified tickers already exist.")

    async def _cmd_remove_ticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /removeticker BBCA TLKM ...")
            return
        result = self.daemon.remove_tickers(context.args)
        removed = result.get('removed', [])
        remaining = result.get('tickers', [])
        if removed:
            await update.message.reply_text(
                f"Removed: {', '.join(removed)}\nRemaining: {', '.join(remaining) if remaining else 'None'}")
        else:
            await update.message.reply_text("None of the specified tickers were found.")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        result = self.daemon.pause()
        await update.message.reply_text(
            f"⏸ *Daemon Paused*\n\nState: {result.get('state', 'unknown')}",
            parse_mode="Markdown"
        )

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        result = self.daemon.resume()
        await update.message.reply_text(
            f"▶️ *Daemon Resumed*\n\nState: {result.get('state', 'unknown')}",
            parse_mode="Markdown"
        )

    async def _cmd_set_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "*Set Token*\n\n"
                "Usage: `/settoken <bearer_token>`\n\n"
                "Delete your message after sending for security.",
                parse_mode="Markdown"
            )
            return

        bearer_token = context.args[0]
        result = self.daemon.set_token_and_reconnect(bearer_token)

        if result.get('success'):
            await update.message.reply_text(
                f"*Token Updated*\n\n{result.get('message', '')}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"*Error*\n\n{result.get('error', 'Unknown error')}",
                parse_mode="Markdown"
            )

    async def _cmd_recap(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        recap = self.daemon.get_daily_recap()
        text = f"*Daily Recap — {recap.get('date', 'Today')}*\n\n"
        tickers = recap.get('tickers', [])
        text += f"Tickers: {', '.join(tickers) if tickers else 'None'}\n"
        text += f"Total Messages: {recap.get('total_messages', 0):,}\n"
        text += f"Reconnects: {recap.get('total_reconnects', 0)}\n\n"
        msg_counts = recap.get('message_counts', {})
        if msg_counts:
            text += "*Per-Ticker:*\n"
            for ticker, count in sorted(msg_counts.items(), key=lambda x: x[1], reverse=True):
                text += f"  `{ticker}`: {count:,}\n"
        if recap.get('next_open'):
            text += f"\nNext session: {recap['next_open'][:19].replace('T', ' ')} WIB"
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_heartbeat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._send_heartbeat(update.effective_chat.id)

    async def _cmd_set_heartbeat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Change heartbeat interval at runtime."""
        if not context.args:
            await update.message.reply_text(
                f"Current interval: *{self.heartbeat_minutes} min*\n\n"
                "Usage: `/setheartbeat <minutes>`",
                parse_mode="Markdown"
            )
            return

        try:
            minutes = int(context.args[0])
            if minutes < 1 or minutes > 1440:
                raise ValueError("out of range")
        except ValueError:
            await update.message.reply_text("Please provide a number between 1 and 1440.")
            return

        old = self.heartbeat_minutes
        self.heartbeat_minutes = minutes

        # reschedule if job queue is available
        jq = self.app.job_queue
        if jq:
            # remove old heartbeat job
            for job in jq.get_jobs_by_name("heartbeat"):
                job.schedule_removal()
            # schedule with new interval
            jq.run_repeating(
                self._job_heartbeat,
                interval=minutes * 60,
                first=60,
                name="heartbeat"
            )

        await update.message.reply_text(
            f"*Heartbeat Updated*\n\n{old} min -> {minutes} min",
            parse_mode="Markdown"
        )

    # ================================================================== #
    #  COMMAND HANDLERS — Historical Trade Jobs
    # ================================================================== #

    async def _cmd_jobs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List recent historical trade jobs."""
        if not self.job_manager:
            await update.message.reply_text("Job manager not available.")
            return

        jobs_list = self.job_manager.list_jobs()
        if not jobs_list:
            await update.message.reply_text("No jobs found.\n\nCreate one with /newjob")
            return

        status_icons = {
            'QUEUED': '🔵', 'RUNNING': '🟢', 'PAUSED': '🟡',
            'COMPLETED': '✅', 'FAILED': '🔴',
        }

        text = f"*Recent Jobs ({len(jobs_list)})*\n\n"
        for j in jobs_list[:10]:
            icon = status_icons.get(j['status'], '❓')
            short_id = j['job_id'][:8]
            tickers_str = ', '.join(j.get('tickers', [])[:3])
            if len(j.get('tickers', [])) > 3:
                tickers_str += f" +{len(j['tickers']) - 3}"
            progress = j.get('tasks', [])
            total = len(progress)
            done = sum(1 for t in progress if t.get('status') in ('COMPLETED', 'SKIPPED'))
            text += f"{icon} `{short_id}` {j['status']} — {tickers_str} ({done}/{total})\n"

        text += "\nUse /jobstatus <id> for details"
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_new_job(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Create a historical trade job.

        Usage: /newjob BBCA,TLKM 2026-01-01 2026-01-31 [delay] [limit]
        """
        if not self.job_manager:
            await update.message.reply_text("Job manager not available.")
            return

        if len(context.args) < 3:
            await update.message.reply_text(
                "*Create Historical Trade Job*\n\n"
                "Usage: `/newjob TICKERS FROM TO [delay] [limit]`\n\n"
                "Example:\n`/newjob BBCA,TLKM 2026-01-01 2026-01-31`\n"
                "`/newjob BBRI 2026-02-01 2026-02-15 2 100`",
                parse_mode="Markdown"
            )
            return

        tickers = [t.strip().upper() for t in context.args[0].split(',') if t.strip()]
        from_date = context.args[1]
        until_date = context.args[2]
        delay = float(context.args[3]) if len(context.args) > 3 else 3.0
        limit = int(context.args[4]) if len(context.args) > 4 else 50

        try:
            datetime.strptime(from_date, '%Y-%m-%d')
            datetime.strptime(until_date, '%Y-%m-%d')
        except ValueError:
            await update.message.reply_text("Invalid date format. Use YYYY-MM-DD.")
            return

        job_id = self.job_manager.create_job(
            tickers=tickers,
            from_date=from_date,
            until_date=until_date,
            delay_seconds=delay,
            limit=limit,
        )

        job = self.job_manager.get_job(job_id)
        total_tasks = len(job.tasks) if job else '?'

        await update.message.reply_text(
            f"*Job Created*\n\n"
            f"ID: `{job_id[:8]}`\n"
            f"Tickers: {', '.join(tickers)}\n"
            f"Range: {from_date} to {until_date}\n"
            f"Tasks: {total_tasks}\n"
            f"Delay: {delay}s | Limit: {limit}",
            parse_mode="Markdown"
        )

    async def _cmd_job_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed status for a job by (partial) ID."""
        if not self.job_manager:
            await update.message.reply_text("Job manager not available.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /jobstatus <job_id>")
            return

        partial_id = context.args[0].lower()
        job = self._find_job_by_partial_id(partial_id)
        if not job:
            await update.message.reply_text(f"No job found matching `{partial_id}`")
            return

        progress = job.get_progress()
        text = (
            f"*Job {job.job_id[:8]}*\n\n"
            f"Status: {job.status.value}\n"
            f"Tickers: {', '.join(job.tickers)}\n"
            f"Range: {job.from_date} to {job.until_date}\n\n"
            f"*Progress*\n"
            f"  Total: {progress['total']}\n"
            f"  Completed: {progress['completed']}\n"
            f"  Failed: {progress['failed']}\n"
            f"  Running: {progress['running']}\n"
            f"  Pending: {progress['pending']}\n"
            f"  Percent: {progress['percentage']}%\n"
        )
        records = sum(t.records_fetched for t in job.tasks)
        text += f"\nRecords fetched: {records:,}"

        if job.started_at:
            text += f"\nStarted: {job.started_at[:19]}"
        if job.completed_at:
            text += f"\nCompleted: {job.completed_at[:19]}"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_pause_job(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.job_manager or not context.args:
            await update.message.reply_text("Usage: /pausejob <job_id>")
            return
        job = self._find_job_by_partial_id(context.args[0].lower())
        if not job:
            await update.message.reply_text("Job not found.")
            return
        self.job_manager.pause_job(job.job_id)
        await update.message.reply_text(f"⏸ Job `{job.job_id[:8]}` paused.", parse_mode="Markdown")

    async def _cmd_resume_job(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.job_manager or not context.args:
            await update.message.reply_text("Usage: /resumejob <job_id>")
            return
        job = self._find_job_by_partial_id(context.args[0].lower())
        if not job:
            await update.message.reply_text("Job not found.")
            return
        self.job_manager.resume_job(job.job_id)
        await update.message.reply_text(f"▶️ Job `{job.job_id[:8]}` resumed.", parse_mode="Markdown")

    async def _cmd_cancel_job(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.job_manager or not context.args:
            await update.message.reply_text("Usage: /canceljob <job_id>")
            return
        job = self._find_job_by_partial_id(context.args[0].lower())
        if not job:
            await update.message.reply_text("Job not found.")
            return
        self.job_manager.cancel_job(job.job_id)
        await update.message.reply_text(f"Job `{job.job_id[:8]}` cancelled.", parse_mode="Markdown")

    def _find_job_by_partial_id(self, partial_id: str):
        """Look up a job by prefix match on its UUID."""
        if not self.job_manager:
            return None
        for jid, job in self.job_manager.jobs.items():
            if jid.lower().startswith(partial_id):
                return job
        return None

    # ================================================================== #
    #  INLINE KEYBOARD HANDLER
    # ================================================================== #

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == "pause":
            result = self.daemon.pause()
            await query.edit_message_text(f"⏸ Daemon paused.\nState: {result.get('state', 'unknown')}")
        elif query.data == "resume":
            result = self.daemon.resume()
            await query.edit_message_text(f"▶️ Daemon resumed.\nState: {result.get('state', 'unknown')}")
        elif query.data == "refresh_status":
            status = self.daemon.get_status()
            state_emojis = {
                'streaming': '🟢', 'waiting_market': '🟡', 'market_closed': '🌙',
                'paused': '⏸️', 'error': '🔴', 'no_tickers': '📋',
            }
            emoji = state_emojis.get(status['state'], '❓')
            text = f"{emoji} State: {status['state'].replace('_', ' ').title()}\n"
            if status.get('stream'):
                mc = status['stream'].get('message_counts', {})
                text += f"Messages: {sum(mc.values()):,}\n"
                text += f"Reconnects: {status['stream'].get('total_reconnects', 0)}\n"
            text += f"Tickers: {', '.join(status.get('tickers', []))}"
            await query.edit_message_text(text)
        elif query.data == "prompt_set_tickers":
            await query.edit_message_text(
                "Send tickers with /settickers command:\n`/settickers BBCA TLKM BMRI`",
                parse_mode="Markdown"
            )
        elif query.data == "prompt_set_token":
            await query.edit_message_text(
                "Send your bearer token:\n`/settoken <your_token>`\n\n"
                "Delete the message after sending.",
                parse_mode="Markdown"
            )

    # ================================================================== #
    #  SCHEDULED JOBS
    # ================================================================== #

    async def _job_heartbeat(self, context: ContextTypes.DEFAULT_TYPE):
        """Only send automatic heartbeats when the market is open or
        opening within the next hour. Silent otherwise."""
        if not self._is_active_instance():
            return
        try:
            status = self.daemon.get_status()
            market = status.get('market', {})
            is_open = market.get('is_open', False)
            secs_until_next = market.get('time_until_next', 99999)

            # fire if market is currently open, or next open is < 1 hour away
            if is_open or secs_until_next <= 3600:
                await self._send_heartbeat(self.chat_id)
        except Exception as e:
            logger.error(f"Heartbeat job error: {e}")

    async def _send_heartbeat(self, chat_id):
        status = self.daemon.get_status()
        state = status['state']
        state_emojis = {
            'streaming': '🟢', 'waiting_market': '🟡', 'market_closed': '🌙',
            'paused': '⏸️', 'error': '🔴', 'no_tickers': '📋',
        }
        emoji = state_emojis.get(state, '❓')
        text = f"*Heartbeat*\n\n{emoji} State: {state.replace('_', ' ').title()}\n"
        tickers = status.get('tickers', [])
        text += f"Tickers: {', '.join(tickers) if tickers else 'None'}\n"

        if status.get('stream'):
            stream = status['stream']
            mc = stream.get('message_counts', {})
            text += (
                f"\nConnection: {stream.get('connection_status', 'unknown')}\n"
                f"Messages: {sum(mc.values()):,}\n"
                f"Reconnects: {stream.get('total_reconnects', 0)}\n"
                f"Uptime: {self._format_uptime(stream.get('uptime_seconds', 0))}\n"
            )
            if mc:
                text += "\n*Lines per ticker:*\n"
                for ticker, count in sorted(mc.items(), key=lambda x: x[1], reverse=True):
                    text += f"  `{ticker}`: {count:,}\n"

        await self.app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

    async def _job_token_reminder(self, context: ContextTypes.DEFAULT_TYPE):
        """Daily 07:30 WIB reminder to set the bearer token (trade days only)."""
        if not self._is_active_instance():
            return
        try:
            now = datetime.now()
            if now.weekday() >= 5:
                return

            # skip if the daemon already has a valid token
            token_status = self.daemon.token_manager.get_status()
            if token_status.get('valid'):
                logger.info("Token still valid, skipping reminder")
                return

            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    "*Token Reminder*\n\n"
                    "Market opens in ~1h 25min.\n"
                    "Please send your bearer token:\n\n"
                    "`/settoken <your_token>`\n\n"
                    "Delete the message after sending."
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Token reminder error: {e}")

    async def _send_daily_recap(self):
        try:
            today = datetime.now().date()
            if self._recap_sent_date == today:
                return

            recap = self.daemon.get_daily_recap()
            text = f"*Daily Market Recap — {recap.get('date', str(today))}*\n\n"
            tickers = recap.get('tickers', [])
            text += f"Active Tickers: {', '.join(tickers) if tickers else 'None'}\n"
            text += f"Total Messages: {recap.get('total_messages', 0):,}\n"
            text += f"Reconnects: {recap.get('total_reconnects', 0)}\n\n"

            msg_counts = recap.get('message_counts', {})
            if msg_counts:
                text += "*Volume per Ticker:*\n"
                for ticker, count in sorted(msg_counts.items(), key=lambda x: x[1], reverse=True):
                    text += f"  `{ticker}`: {count:,}\n"

            if recap.get('next_open'):
                text += f"\nNext session: {recap['next_open'][:19].replace('T', ' ')} WIB"

            text += "\n\n*Bot entering night mode* (heartbeats paused until 08:30 WIB)"

            await self.app.bot.send_message(
                chat_id=self.chat_id, text=text, parse_mode="Markdown"
            )
            self._recap_sent_date = today
            logger.info("Daily recap sent")
        except Exception as e:
            logger.error(f"Failed to send daily recap: {e}", exc_info=True)

    async def _job_pre_market(self, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_active_instance():
            return
        try:
            status = self.daemon.get_status()
            tickers = status.get('tickers', [])
            text = "*Pre-Market Update*\n\nMarket opens in ~30 minutes!\n\n"
            if tickers:
                text += "*Streaming tickers:*\n"
                for t in tickers:
                    text += f"  `{t}`\n"
                text += f"\nTotal: {len(tickers)} tickers"
            else:
                text += "No tickers configured! Add tickers with /settickers"
            keyboard = [[InlineKeyboardButton("View Tickers", callback_data="refresh_status")]]
            await self.app.bot.send_message(
                chat_id=self.chat_id, text=text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Pre-market job error: {e}")

    async def _job_daily_recap(self, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_active_instance():
            return
        await self._send_daily_recap()

    # ---- Google Drive scheduled uploads ----

    async def _job_gdrive_post_market(self, context: ContextTypes.DEFAULT_TYPE):
        """16:15 WIB — upload today's orderbook files to Drive."""
        if not self._is_active_instance():
            return
        today = datetime.now().strftime('%Y-%m-%d')
        await self._run_gdrive_upload(today)

    async def _job_gdrive_midnight(self, context: ContextTypes.DEFAULT_TYPE):
        """00:05 WIB — catch-all re-upload for yesterday's files."""
        if not self._is_active_instance():
            return
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        await self._run_gdrive_upload(yesterday)

    async def _run_gdrive_upload(self, date_str: str):
        """Run the actual upload and send a Telegram summary."""
        if not self.gdrive_uploader or not self.orderbook_dir:
            logger.debug("GDrive uploader or orderbook_dir not configured, skipping upload")
            return

        try:
            result = self.gdrive_uploader.upload_orderbook_day(date_str, self.orderbook_dir)
            uploaded = result.get('uploaded', 0)
            failed = result.get('failed', 0)
            skipped = result.get('skipped', 0)
            total_bytes = result.get('total_bytes', 0)
            size_mb = total_bytes / (1024 * 1024)

            if uploaded == 0 and failed == 0 and skipped == 0:
                logger.info(f"GDrive: no files to upload for {date_str}")
                return

            if result['success']:
                text = (
                    f"*Upload Complete*\n\n"
                    f"Date: {date_str}\n"
                    f"Uploaded: {uploaded}  |  Skipped: {skipped}  |  Failed: {failed}\n"
                    f"Size: {size_mb:.1f} MB\n"
                )
            else:
                text = (
                    f"*Upload Partial Failure*\n\n"
                    f"Date: {date_str}\n"
                    f"Uploaded: {uploaded}  |  Failed: {failed}\n"
                )
                for r in result.get('results', []):
                    if not r.get('success') and not r.get('skipped'):
                        text += f"  {r['file']}: {r.get('error', '?')}\n"

            await self.app.bot.send_message(
                chat_id=self.chat_id, text=text, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"GDrive upload job error for {date_str}: {e}", exc_info=True)
            try:
                await self.app.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"*Upload Failed*\n\nDate: {date_str}\nError: {e}",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    # ================================================================== #
    #  DAEMON CALLBACKS (orderbook)
    # ================================================================== #

    def _on_reconnect_alert(self, consecutive_count):
        if not self.running or not self.app or not self._loop:
            return
        if not self._is_active_instance():
            return

        async def send_alert():
            try:
                status = self.daemon.get_status()
                text = (
                    f"*Reconnection Alert*\n\n"
                    f"Consecutive reconnects: {consecutive_count}\n"
                    f"State: {status['state']}\n"
                )
                if status.get('stream') and status['stream'].get('last_error'):
                    text += f"Last error: {status['stream']['last_error']}\n"
                text += "\nThe daemon will keep trying to reconnect automatically."
                keyboard = [[InlineKeyboardButton("Check Status", callback_data="refresh_status")]]
                await self.app.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logger.error(f"Failed to send reconnect alert: {e}")

        self._send_async(send_alert())

    def _on_state_change(self, old_state, new_state):
        significant_changes = {'streaming', 'error'}
        if new_state.value not in significant_changes:
            return
        if not self.running or not self.app or not self._loop:
            return
        if not self._is_active_instance():
            return

        async def send_notification():
            try:
                state_emojis = {
                    'streaming': '🟢', 'waiting_market': '🟡', 'market_closed': '🌙',
                    'paused': '⏸️', 'error': '🔴', 'no_tickers': '📋',
                }
                emoji = state_emojis.get(new_state.value, '❓')
                old_emoji = state_emojis.get(old_state.value, '❓')
                text = (
                    f"*State Changed*\n\n"
                    f"{old_emoji} {old_state.value.replace('_', ' ').title()}"
                    f" -> {emoji} {new_state.value.replace('_', ' ').title()}"
                )
                await self.app.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send state change notification: {e}")

        self._send_async(send_notification())

    # ================================================================== #
    #  JOB MANAGER CALLBACK (historical trades)
    # ================================================================== #

    def _on_job_event(self, event: str, data: dict):
        """Callback registered with JobManager for lifecycle notifications."""
        if not self.running or not self._loop:
            return
        if not self._is_active_instance():
            return

        async def _send():
            try:
                text = self._format_job_event(event, data)
                if not text:
                    return

                keyboard = None
                if event == 'job_paused' and data.get('reason') == 'Token expired':
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Set Token", callback_data="prompt_set_token")]
                    ])

                await self.app.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode="Markdown",
                    reply_markup=keyboard
                )

                # if job completed, try uploading its output to drive
                if event == 'job_completed' and self.gdrive_uploader:
                    await self._upload_job_output(data)

            except Exception as e:
                logger.error(f"Failed to send job event notification: {e}")

        self._send_async(_send())

    def _format_job_event(self, event: str, data: dict) -> str:
        short_id = data.get('job_id', '?')[:8]
        tickers = ', '.join(data.get('tickers', []))

        if event == 'job_started':
            return (
                f"*Job Started*\n\n"
                f"ID: `{short_id}`\n"
                f"Tickers: {tickers}\n"
                f"Range: {data.get('from_date')} to {data.get('until_date')}\n"
                f"Tasks: {data.get('total_tasks', '?')}"
            )
        elif event == 'job_progress':
            return (
                f"*Job Progress*\n\n"
                f"ID: `{short_id}` — {data.get('percentage', 0):.0f}%\n"
                f"Completed: {data.get('completed', 0)}/{data.get('total', 0)}\n"
                f"Failed: {data.get('failed', 0)}"
            )
        elif event == 'job_completed':
            elapsed = ''
            if data.get('started_at') and data.get('completed_at'):
                try:
                    start = datetime.fromisoformat(data['started_at'])
                    end = datetime.fromisoformat(data['completed_at'])
                    elapsed = f"\nDuration: {self._format_uptime((end - start).total_seconds())}"
                except Exception:
                    pass
            return (
                f"*Job Completed*\n\n"
                f"ID: `{short_id}`\n"
                f"Tickers: {tickers}\n"
                f"Tasks: {data.get('completed_tasks', 0)}/{data.get('total_tasks', 0)}\n"
                f"Records: {data.get('total_records', 0):,}\n"
                f"Failed: {data.get('failed_tasks', 0)}"
                f"{elapsed}"
            )
        elif event == 'job_failed':
            return (
                f"*Job Failed*\n\n"
                f"ID: `{short_id}`\n"
                f"Tickers: {tickers}\n"
                f"Error: {data.get('error', 'Unknown')}"
            )
        elif event == 'job_paused':
            return (
                f"*Job Paused*\n\n"
                f"ID: `{short_id}`\n"
                f"Tickers: {tickers}\n"
                f"Reason: {data.get('reason', 'Unknown')}"
            )
        return None

    async def _upload_job_output(self, data: dict):
        """After a job completes, upload its CSV to Google Drive."""
        if not self.gdrive_uploader:
            return
        try:
            from config import DATA_DIR
            tickers = data.get('tickers', [])
            from_date = data.get('from_date', '')
            until_date = data.get('until_date', '')

            uploaded = []
            for ticker in tickers:
                filename = f"{ticker}_{from_date}_{until_date}.csv"
                filepath = DATA_DIR / filename
                if filepath.exists():
                    result = self.gdrive_uploader.upload_job_output(filepath)
                    if result.get('success') and not result.get('skipped'):
                        uploaded.append(filename)

            if uploaded:
                text = (
                    f"*Job Output Uploaded*\n\n"
                    f"Files: {len(uploaded)}\n"
                )
                for f in uploaded:
                    text += f"  {f}\n"
                await self.app.bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Failed to upload job output: {e}")

    # ================================================================== #
    #  UTILITIES
    # ================================================================== #

    @staticmethod
    def _format_uptime(seconds):
        if not seconds or seconds < 60:
            return f"{int(seconds or 0)}s"
        if seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"
