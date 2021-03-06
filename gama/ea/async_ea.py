import logging
from functools import partial
import time

import stopit

from gama.utilities.logging_utilities import TOKENS, log_parseable_event, default_time_format
from ..utilities.logging_utilities import MultiprocessingLogger
from gama.utilities.generic.function_dispatcher import FunctionDispatcher

log = logging.getLogger(__name__)


def _safe_outside_call(fn, timeout):
    """ Calls fn and log any exception it raises without reraising, except for TimeoutException. """
    try:
        fn()
    except stopit.utils.TimeoutException:
        raise
    except Exception:
        # We actually want to catch any other exception here, because the callback code can be
        # arbitrary (it can be provided by users). This excuses the catch-all Exception.
        # Note that KeyboardInterrupts are not exceptions and get elevated to the caller.
        log.warning("Exception during callback.", exc_info=True)
        pass
    if timeout():
        log.warning("Time exceeded during callback, but exception was swallowed.")
        raise stopit.utils.TimeoutException


def async_ea(objectives, start_population, toolbox, evaluation_callback=None, restart_callback=None,
             elimination_callback=None, max_n_evaluations=10000, max_time_seconds=1e7, n_jobs=1):
    if max_time_seconds <= 0 or max_time_seconds > 3e6:
        raise ValueError("'max_time_seconds' must be greater than 0 and less than or equal to 3e6, but was {}."
                         .format(max_time_seconds))
    if max_n_evaluations <= 0:
        raise ValueError("'n_evaluations' must be non-negative, but was {}.".format(max_n_evaluations))
    if n_jobs <= 0:
        raise ValueError("'n_jobs' must be non-negative, but was {}.".format(n_jobs))

    start_time = time.time()
    max_population_size = len(start_population)
    queued_individuals_str = set()
    queued_individuals = {}
    logger = MultiprocessingLogger() if n_jobs > 1 else log
    evaluation_dispatcher = FunctionDispatcher(n_jobs, partial(toolbox.evaluate, logger=logger))
    evaluation_dispatcher.start()

    def exceed_timeout():
        return (time.time() - start_time) > max_time_seconds

    def queue_individual_for_evaluation(individual):
        """ Place an individual in the queue for evaluation if it compiles and is not yet queued. """
        if str(individual) not in queued_individuals_str:
            queued_individuals_str.add(str(individual))
            compiled_individual = toolbox.compile(individual)
            if compiled_individual is not None:
                identifier = evaluation_dispatcher.queue_evaluation(compiled_individual)
                queued_individuals[identifier] = individual
                return True
        return False

    def get_next_evaluation_result():
        """ Get a new evaluation result, process it and assign it to the correct individual. """
        identifier, output, _ = evaluation_dispatcher.get_next_result()
        individual = queued_individuals[identifier]
        score, start_time, evaluation_time, length = output
        if len(objectives) == 1:
            individual.fitness.values = (score,)
        elif objectives[1] == 'time':
            individual.fitness.values = (score, evaluation_time)
        elif objectives[1] == 'size':
            individual.fitness.values = (score, length)

        individual.fitness.start_time = start_time
        individual.fitness.time = evaluation_time

        if n_jobs > 1:
            logger.flush_to_log(log)
        return individual

    with stopit.ThreadingTimeout(max_time_seconds) as c_mgr:
        should_restart = True
        while should_restart:
            should_restart = False
            current_population = []

            log.info('Starting EA with new population.')
            for individual in start_population:
                queue_individual_for_evaluation(individual)

            for ind_no in range(max_n_evaluations):
                individual = get_next_evaluation_result()
                log_parseable_event(log, TOKENS.EVALUATION_RESULT, individual.fitness.start_time,
                                    individual.fitness.time, individual.fitness.wvalues, individual.id, individual)

                if evaluation_callback:
                    _safe_outside_call(partial(evaluation_callback, individual), exceed_timeout)

                should_restart = (restart_callback is not None and restart_callback())
                if should_restart:
                    log.info("Restart criterion met. Restarting with new random population.")
                    log_parseable_event(log, TOKENS.EA_RESTART, ind_no)
                    start_population = toolbox.population(n=max_population_size)
                    break

                current_population.append(individual)
                if len(current_population) > max_population_size:
                    to_remove = toolbox.eliminate(current_population, 1)
                    log_parseable_event(log, TOKENS.EA_REMOVE_IND, to_remove)
                    current_population.remove(to_remove[0])
                    if elimination_callback:
                        _safe_outside_call(partial(elimination_callback, to_remove[0]), exceed_timeout)

                if len(current_population) > 1:
                    for _ in range(50):
                        new_individual = toolbox.create(current_population, 1)[0]
                        if queue_individual_for_evaluation(new_individual):
                            break
                    else:
                        log.warning('Unable to create new individual.')

            evaluation_dispatcher.restart()

    # If the function is terminated early by way of a KeyboardInterrupt, there is no need to communicate to the
    # evaluation processes to shut down, since they handle the KeyboardInterrupt directly.
    # The function should not be terminated early by way of another exception, if it does, it should crash loud.
    evaluation_dispatcher.stop()
    if not c_mgr:
        log.info('Asynchronous EA terminated because maximum time has elapsed.'
                 '{} individuals have been evaluated.'.format(ind_no))
    return current_population
