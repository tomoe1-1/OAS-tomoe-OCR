"""Single-round battles for the current version; legacy exploration stays intact."""
from module.exception import TaskRecoveryFailed
from module.logger import logger
from tasks.GameUi.navigator import GameUi
from tasks.GameUi.default_pages import (page_activity_stage, page_version_trial,
                                       page_activity_map_home, page_battle_result, page_reward)
from tasks.GameUi.version_activity_ui import C_CHALLENGE, _click_once

class VersionExplorationAct:
    def run_exploration(self):
        options=self.conf.version_exploration_config
        if not options.battle_selection:
            return super().run_exploration()
        for mode in options.sequence_v:
            limit=getattr(options,mode+'_limit')
            for count in range(limit):
                if self.time_limit_reached(): return
                if not self._run_version_round(mode): return
                logger.info(f'Version exploration {mode}: settled {count+1}/{limit}')

    def _version_settlement(self):
        return GameUi.detect_page_in(self,page_battle_result,page_reward,
                                     include_global=False) is not None

    def _version_confirm_result(self):
        if self.current_action_type.startswith('version_'):
            self._ui_verify(self._version_settlement,[],timeout=5)
            self._version_result_seen=True

    def _handle_result(self,context,config):
        self._version_confirm_result()
        return super()._handle_result(context,config)

    def _handle_reward(self,context,config):
        self._version_confirm_result()
        return super()._handle_reward(context,config)

    def _run_version_round(self,mode):
        if mode not in ('ruins','trial'): raise ValueError('Unknown version battle: '+mode)
        page=page_activity_stage if mode=='ruins' else page_version_trial
        self.current_action_type='version_'+mode
        self.goto_page(page)
        self._ui_verify(lambda:self.match_page_once(page),[],timeout=10)
        if self.time_limit_reached(): return False
        config=getattr(self.conf,'version_'+mode+'_battle_conf').model_copy(deep=True)
        config.continuous_battle=False
        self._version_result_seen=False
        _click_once(self,C_CHALLENGE)
        # No challenge retry: a fast result/reward is a valid entry state.
        self._ui_verify(lambda:self.is_in_battle(False) or self._version_settlement(),
                        [C_CHALLENGE],timeout=30)
        self.run_general_battle(config,battle_key='version_'+mode,
            exit_matcher=lambda:self._version_result_seen and (
                self.match_page_once(page) or self.match_page_once(page_activity_map_home)))
        if not self._version_result_seen:
            raise TaskRecoveryFailed('Version battle returned without observed settlement')
        self._ui_verify(lambda:self.match_page_once(page) or self.match_page_once(page_activity_map_home),
                        [],timeout=15)
        return True
