""" Contains classes and function(s) which help define automated machine 
learning as a genetic programming problem.
(Yes, I need to find a better file name.)
"""
from collections import defaultdict
import logging

import numpy as np
from deap import gp, tools
import sklearn
from sklearn.pipeline import Pipeline

from ..ea.modified_deap import gen_grow_safe

log = logging.getLogger(__name__)


class Data(np.ndarray):
    """ Dummy class that represents a dataset."""
    pass 


class Predictions(np.ndarray):
    """ Dummy class that represents prediction data. """
    pass


def pset_from_config(configuration):
    """ Create a pset for the given configuration dictionary.
    
    Given a configuration dictionary specifying operators (e.g. sklearn 
    estimators), their hyperparameters and values for each hyperparameter,
    create a gp.PrimitiveSetTyped that contains:
        - For each operator a primitive
        - For each possible hyperparameter-value combination a unique terminal
        
    Side effect: Imports the classes of each primitive.
        
    Returns the given Pset.
    """
    pset = gp.PrimitiveSetTyped("pipeline", in_types=[Data], ret_type=Predictions)
    parameter_checks = {}
    pset.renameArguments(ARG0="data")
    
    shared_hyperparameter_types = {}
    # We have to make sure the str-keys are evaluated first: they describe shared hyperparameters
    # We can not rely on order-preserving dictionaries as this is not in the Python 3.5 specification.
    sorted_keys = reversed(sorted(configuration.keys(), key=lambda x: str(type(x))))
    for key in sorted_keys:
        values = configuration[key]
        if isinstance(key, str):
            # Specification of shared hyperparameters
            hyperparameter_type = type(str(key), (object,), {})
            shared_hyperparameter_types[key] = hyperparameter_type
            for value in values:
                # Escape string values with quotes
                value_str = "'{}'".format(value) if isinstance(value, str) else str(value)
                hyperparameter_str = "{}={}".format(key, value_str)
                pset.addTerminal(value, hyperparameter_type, hyperparameter_str)
        elif isinstance(key, object):
            #Specification of operator (learner, preprocessor)
            hyperparameter_types = []
            for name, param_values in sorted(values.items()):
                # We construct a new type for each hyperparameter, so we can specify
                # it as terminal type, making sure it matches with expected
                # input of the operators. Moreover it automatically makes sure that
                # crossover only happens between same hyperparameters.
                if isinstance(param_values, list) and not param_values:
                    # An empty list indicates a shared hyperparameter
                    hyperparameter_types.append(shared_hyperparameter_types[name])
                elif name == "param_check":
                    # This allows users to define illegal hyperparameter combinations, but is not a terminal.
                    parameter_checks[key.__name__] = param_values[0]
                else:                
                    hyperparameter_type = type("{}{}".format(key.__name__, name), (object,), {})
                    hyperparameter_types.append(hyperparameter_type)
                    for value in param_values:
                        # Escape string values with quotes otherwise they are variables
                        value_str = ("'{}'".format(value) if isinstance(value, str)
                                     else "{}".format(value.__name__) if callable(value)
                                     else str(value))
                        hyperparameter_str = "{}.{}={}".format(key.__name__, name, value_str)
                        pset.addTerminal(value, hyperparameter_type, hyperparameter_str)

            # After registering the hyperparameter types, we can register the operator itself.
            transformer_tags = ["DATA_PREPROCESSING", "FEATURE_SELECTION", "DATA_TRANSFORMATION"]
            if (issubclass(key, sklearn.base.TransformerMixin) or
                  (hasattr(key, 'metadata') and key.metadata.query()["primitive_family"] in transformer_tags)):
                pset.addPrimitive(key, [Data, *hyperparameter_types], Data)
            elif (issubclass(key, sklearn.base.ClassifierMixin) or
                  (hasattr(key, 'metadata') and key.metadata.query()["primitive_family"] == "CLASSIFICATION")):
                pset.addPrimitive(key, [Data, *hyperparameter_types], Predictions)
            elif (issubclass(key, sklearn.base.RegressorMixin) or
                  (hasattr(key, 'metadata') and key.metadata.query()["primitive_family"] == "REGRESSION")):
                pset.addPrimitive(key, [Data, *hyperparameter_types], Predictions)
            else:
                raise TypeError("Expected {} to be either subclass of "
                                "TransformerMixin, RegressorMixin or ClassifierMixin.".format(key))
        else:
            raise TypeError('Encountered unknown type as key in dictionary.'
                            'Keys in the configuration should be str or class.')
    
    return pset, parameter_checks


def compile_individual(expr, pset, parameter_checks=None, preprocessing_steps=None):
    """ Compile the individual to a sklearn pipeline."""
    # TODO: expr only for compatibility
    ind = expr
    components = []
    name_counter = defaultdict(int)
    while len(ind) > 0:
        prim, remainder = ind[0], ind[1:]
        if isinstance(prim, gp.Terminal):
            if len(remainder) > 0:
                raise Exception
            break

        try:
            component, n_kwargs = expression_to_component(prim, reversed(remainder), pset, parameter_checks)
        except ValueError:
            return None

        # Each component in the pipeline must have a unique name.
        name = prim.name + str(name_counter[prim.name])
        name_counter[prim.name] += 1

        components.append((name, component))
        if n_kwargs == 0:
            ind = ind[1:]
        else:
            ind = ind[1:-n_kwargs]

    if preprocessing_steps:
        for step in reversed(preprocessing_steps):
            components.append((step.__class__.__name__, step))
    return Pipeline(list(reversed(components)))


def expression_to_component(primitive, terminals, pset, parameter_checks=None):
    """ Creates Python-object for the primitive-terminals combination.

    It is allowed to have trailing terminals in the list, they will be ignored.

    Returns an instantiated python object and the number of terminals used.
    """
    # See if all terminals have a value provided (except Data Terminal)
    required = reversed([terminal for terminal in primitive.args if not terminal.__name__ == 'Data'])
    required_provided = list(zip(required, terminals))
    if not all(r == p.ret for (r, p) in required_provided):
        print([(r, p.ret) for (r,p) in required_provided])
        raise ValueError('Missing {}-terminal for {}-primitive.')

    def extract_arg_name(terminal_name):
        equal_idx = terminal_name.rfind('=')
        start_parameter_name = terminal_name.rfind('.', 0, equal_idx) + 1
        return terminal_name[start_parameter_name:equal_idx]

    kwargs = {
        extract_arg_name(p.name): pset.context[p.name]
        for r, p in required_provided
    }

    primitive_class = pset.context[primitive.name]

    if (parameter_checks is not None
            and primitive.name in parameter_checks
            and not parameter_checks[primitive.name](kwargs)):
        raise ValueError('Not a valid configuration according to the provided parameter check.')

    return primitive_class(**kwargs), len(kwargs)


def generate_valid(pset, min_, max_, toolbox):
    """ Generates a valid pipeline. """
    for _ in range(50):
        ind = gen_grow_safe(pset, min_, max_)
        pl = toolbox.compile(ind)
        if pl is not None:
            return ind
    raise Exception


def individual_length(individual):
    """ Gives a measure for the length of the pipeline. Currently, this is the number of primitives. """
    return len([el for el in individual if isinstance(el, gp.Primitive)])


def eliminate_NSGA(pop, n):
    return tools.selNSGA2(pop, k=len(pop))[-n:]


def eliminate_worst(pop, n):
    return list(sorted(pop, key=lambda x: x.fitness.wvalues[0]))[-n:]


def offspring_mate_or_mutate(pop, n, cxpb, mutpb, toolbox):
    """ Creates n new individuals based on the population. Can apply both crossover and mutation. """
    offspring = []
    for _ in range(n):
        ind1, ind2 = np.random.choice(range(len(pop)), size=2, replace=False)
        ind1, ind2 = toolbox.clone(pop[ind1]), toolbox.clone(pop[ind2])
        if np.random.random() < cxpb:
            ind1, ind2 = toolbox.mate(ind1, ind2)
        elif np.random.random() < mutpb:
            ind1, = toolbox.mutate(ind1)
        offspring.append(ind1)
    return offspring
