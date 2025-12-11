from copy import deepcopy
from functools import wraps
import logging
import random
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from laboratory import exceptions
from laboratory.observation import Observation
from laboratory.result import Result


logger = logging.getLogger(__name__)


class Experiment(object):
    '''
    Experiment base class. Handles running your control and candidate functions.
    Should be subclassed to add publishing functionality.

    :ivar string name: Experiment name
    :ivar bool raise_on_mismatch: Raise :class:`MismatchException` when
     experiment results do not match
    '''

    name: str
    raise_on_mismatch: bool
    _context: Dict[str, Any]
    _control: Optional[Dict[str, Any]]
    _candidates: List[Dict[str, Any]]

    def __init__(
        self,
        name: str = 'Experiment',
        context: Optional[Dict[str, Any]] = None,
        raise_on_mismatch: bool = False
    ) -> None:
        '''
        :param string name: Experiment name
        :param dict context: Experiment-wide context
        :param bool raise_on_mismatch: Raise if results do not match
        '''
        self.name = name
        self._context = context or {}
        self.raise_on_mismatch = raise_on_mismatch
        self._control = None
        self._candidates = []


    def control(
        self,
        control_func: Callable[..., Any],
        args: Optional[Tuple[Any, ...]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        name: str = 'Control',
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        '''
        Set the experiment's control function. Must be set before ``conduct()`` is called.

        :param callable control_func: your control function
        :param iterable args: positional arguments to pass to your function
        :param dict kwargs: keyword arguments to pass to your function
        :param string name: a name for your observation
        :param dict context: observation-specific context

        :raises LaboratoryException: If attempting to set a second control case
        '''
        if self._control is not None:
            raise exceptions.LaboratoryException(
                'You have already established a control case'
            )

        self._control = {
            'func': control_func,
            'args': args or [],
            'kwargs': kwargs or {},
            'name': name,
            'context': context or {},
        }

    def candidate(
        self,
        cand_func: Callable,
        args: Optional[Tuple[Any, ...]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        name: str = 'Candidate',
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        '''
        Adds a candidate function to an experiment. Can be used multiple times for
        multiple candidates.

        :param callable cand_func: your control function
        :param iterable args: positional arguments to pass to your function
        :param dict kwargs: keyword arguments to pass to your function
        :param string name: a name for your observation
        :param dict context: observation-specific context
        '''
        self._candidates.append({
            'func': cand_func,
            'args': args or [],
            'kwargs': kwargs or {},
            'name': name,
            'context': context or {},
        })

    def conduct(self, randomize: bool = True) -> Any:
        '''
        Run control & candidate functions and return the control's return value.
        ``control()`` must be called first.

        :param bool randomize: controls whether we shuffle the order
            of execution between control and candidate
        :raise LaboratoryException: when no control case has been set
        :return: Control function's return value
        '''
        if self._control is None:
            raise exceptions.LaboratoryException(
                'Your experiment must contain a control case'
            )


        # execute control and exit if experiment is not enabled
        if not self.enabled():
            control = self._run_tested_func(raise_on_exception=True, **self._control)
            return control.value

        def get_func_executor(
            obs_def: Dict[str, Any],
            is_control: bool
        ) -> Callable[[], Tuple[Observation, bool]]:
            return lambda *a, **kw: (
                self._run_tested_func(raise_on_exception=is_control, **obs_def),
                is_control
            )

        funcs: List[Callable[[], Tuple[Observation, bool]]] = [
            get_func_executor(self._control, is_control=True),
        ] + [get_func_executor(cand, is_control=False) for cand in self._candidates]

        if randomize:
            random.shuffle(funcs)

        control: Optional[Observation] = None
        candidates: List[Observation] = []

        for func in funcs:
            observation, is_control = func()
            if is_control:
                control = observation
            else:
                candidates.append(observation)

        if control is None:
            raise exceptions.LaboratoryException("Control observation failed to run.")

        result = Result(self, control, candidates)

        try:
            self.publish(result)
        except Exception:
            msg = 'Exception occurred when publishing %s experiment data'
            logger.exception(msg % self.name)

        return control.value

    def enabled(self) -> bool:
        '''
        Enable the experiment? If false candidates will not be executed.

        :rtype: bool
        '''
        return True

    def compare(self, control: Observation, candidate: Observation) -> bool:
        '''
        Compares two :class:`Observation` instances.

        :param Observation control: The control block's :class:`Observation`
        :param Observation candidate: A candidate block's :class:`Observation`

        :raises MismatchException: If ``Experiment.raise_on_mismatch`` is True

        :return bool: match?
        '''
        if candidate.failure or control.value != candidate.value:
            return self._handle_comparison_mismatch(control, candidate)

        return True

    def publish(self, result: Result) -> None:
        '''
        Publish the results of an experiment.
        This is called after each experiment run.
        Exceptions that occur during publishing will be caught, but logged.

        By default this is a no-op. See :ref:`publishing`.

        :param Result result: The result of an experiment run
        '''
        return

    def get_context(self) -> Dict[str, Any]:
        '''
        :return dict: Experiment-wide context
        '''
        return self._context

    def _run_tested_func(
        self,
        func: Callable,
        args: List[Any],
        kwargs: Dict[str, Any],
        name: str,
        context: Dict[str, Any],
        raise_on_exception: bool
    ) -> Observation:
        ctx = deepcopy(self.get_context())
        ctx.update(context)

        obs = Observation(name, ctx)
        obs.set_start_time()

        try:
            obs.record(func(*args, **kwargs))
        except Exception as ex:
            obs.set_exception(ex)
            if raise_on_exception:
                raise
        finally:
            obs.set_end_time()

        return obs

    def _handle_comparison_mismatch(
        self,
        control: Observation,
        observation: Observation
    ) -> bool:
        if self.raise_on_mismatch:
            if observation.failure:
                tb = ''.join(traceback.format_exception(*observation.exc_info))
                msg = '%s raised an exception:\n%s' % (observation.name, tb)
            else:
                msg = '%s does not match control value (%s != %s)' % (
                    observation.name, control.value, observation.value
                )
            raise exceptions.MismatchException(msg)

        return False
