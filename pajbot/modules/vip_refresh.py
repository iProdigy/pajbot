import logging

from pajbot.apiwrappers.authentication.token_manager import NoTokenError
from pajbot.managers.db import DBManager
from pajbot.managers.schedule import ScheduleManager
from pajbot.models.command import Command, CommandExample
from pajbot.modules import BaseModule, ModuleType
from pajbot.utils import time_method

from requests import HTTPError
from sqlalchemy import text

log = logging.getLogger(__name__)


class VIPRefreshModule(BaseModule):
    ID = __name__.split(".")[-1]
    NAME = "VIP refresh"
    DESCRIPTION = "Regularly updates data about who is VIP"
    ENABLED_DEFAULT = True
    HIDDEN = True
    MODULE_TYPE = ModuleType.TYPE_ALWAYS_ENABLED
    CATEGORY = "Internal"

    UPDATE_INTERVAL = 10  # minutes

    def __init__(self, bot):
        super().__init__(bot)
        self.scheduled_job = None

    def update_vip_cmd(self, bot, source, **rest):
        # TODO if you wanted to improve this: Provide the user with feedback
        #   whether the update succeeded, and if yes, how many users were updated
        bot.whisper(source, "Reloading list of VIPs...")
        bot.action_queue.submit(self._update_vips)

    @time_method
    def _update_vips(self):
        if self.bot is None:
            log.error("_update_vips failed in VIPRefreshModule because bot is None")
            return

        try:
            vips = self.bot.twitch_helix_api.fetch_all_vips(
                self.bot.streamer.id, self.bot.streamer_access_token_manager
            )
        except NoTokenError:
            log.error(
                "Cannot fetch VIPs because no streamer token is present. Have the streamer login with the /streamer_login web route to enable VIP fetch."
            )
            return
        except HTTPError as e:
            if e.response.status_code == 401:
                log.error(
                    "Cannot fetch VIPs because no streamer token is present. Have the streamer login with the /streamer_login web route to enable VIP fetch."
                )
                return
            else:
                log.error(f"Failed to update VIPs: {e} - {e.response.text}")
                return

        with DBManager.create_session_scope() as db_session:
            db_session.execute(
                text(
                    """
CREATE TEMPORARY TABLE vips(
    id TEXT PRIMARY KEY NOT NULL,
    login TEXT NOT NULL,
    name TEXT NOT NULL
)
ON COMMIT DROP"""
                )
            )

            if len(vips) > 0:
                db_session.execute(
                    text("INSERT INTO vips(id, login, name) VALUES (:id, :login, :name)"),
                    [basics.jsonify() for basics in vips],
                )

            # hint to understand this query: "excluded" is a PostgreSQL keyword that referers
            # to the data we tried to insert but failed (so excluded.login would be equal to :login
            # if we only had one value for :login)
            db_session.execute(
                text(
                    """
WITH updated_users AS (
    INSERT INTO "user"(id, login, name, vip)
        SELECT id, login, name, TRUE FROM vips
    ON CONFLICT (id) DO UPDATE SET
        login = excluded.login,
        name = excluded.name,
        vip = TRUE
    RETURNING id
)
UPDATE "user"
SET
    vip = FALSE
WHERE
    id NOT IN (SELECT * FROM updated_users) AND
    vip IS TRUE"""
                )
            )

        log.info(f"Successfully updated {len(vips)} VIPs")

    def load_commands(self, **options):
        self.commands["reload"] = Command.multiaction_command(
            command="reload",
            commands={
                "vips": Command.raw_command(
                    self.update_vip_cmd,
                    delay_all=120,
                    delay_user=120,
                    level=1000,
                    examples=[
                        CommandExample(
                            None,
                            "Reload who is a Twitch channel VIP",
                            chat="user:!reload vips\nbot>user: Reloading list of VIPs...",
                        ).parse()
                    ],
                )
            },
        )

    def enable(self, bot):
        # Web interface, nothing to do
        if not bot:
            return

        # every 10 minutes, send a helix request to get VIPs
        self.scheduled_job = ScheduleManager.execute_every(
            self.UPDATE_INTERVAL * 60, lambda: self.bot.execute_now(self._update_vips)
        )

    def disable(self, bot):
        # Web interface, nothing to do
        if not bot:
            return

        self.scheduled_job.remove()
