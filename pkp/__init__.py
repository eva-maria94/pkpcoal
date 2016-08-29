'''
PKP Pyrolysis Kinetic Preprocessor
==================================
'''
from __future__ import division, absolute_import
from __future__ import print_function, unicode_literals

from autologging import logged
import ruamel_yaml as yaml
import os

from pkp.cpd import CPD
from pkp.polimi import Polimi
import pkp.evolution
import numpy as np

import matplotlib.pyplot as plt
try:
    plt.style.use('mystyle')
except:
    plt.style.use('ggplot')

colors = plt.rcParams['axes.prop_cycle'].by_key()['color']

models = ['CPD', 'Polimi']


@logged
class ReadConfiguration(object):
    '''
    Read configuration file for PKP
    '''

    def __init__(self, yml):
        super(ReadConfiguration, self).__init__()
        if isinstance(yml, (str, unicode)):
            with open(yml, 'r') as f:
                yml_input = yaml.load(f)
        elif isinstance(yml, dict):
            yml_input = yml
        else:
            raise ValueError('Define yml as file name or dictionary')

        # coal settings
        coal_settings = yml_input['Coal']
        self.proximate_analysis = coal_settings['proximate_analysis']
        self.ultimate_analysis = coal_settings['ultimate_analysis']
        # convert HHV from MJ/kg to J/kg
        self.HHV = coal_settings['HHV'] * 1e6
        self.rho_dry = coal_settings['rho_dry']

        # Solver settings
        [setattr(self, model, yml_input[model])
         for model in models]

        # Solver settings
        self.operating_conditions = yml_input['operating_conditions']

        # fit settings
        self.fit_settings = yml_input['FIT']


@logged
class PKPRunner(ReadConfiguration):
    '''
    Run PKP case
    '''
    models = models

    def run(self, results_dir=None, n_p=1):
        '''
        Run detailed models and fit them.

        Parameters
        ----------
        results_dir: str, default=None
            Directory where results are stored. If None is specified is
            used the directory from where PKP is launched.
        np: int, default=1
            Number of processors for evolution fitting
        '''
        results_dir = self.set_results_dir(results_dir)
        self.__log.info('Run models %s', self.models)
        run_results = {}
        fit_results = {}
        for model in self.models:
            self.__log.debug('Model %s', model)
            model_settings = getattr(self, model)
            results = self._run_model(model=model,
                                      results_dir=results_dir)
            if results:
                run_results[model] = results
                self.__log.debug('Finish run %s %s', model,
                                 results.keys())
            if model_settings['fit']:
                fit_results[model] = {}
                for fitname, fit in model_settings['fit'].iteritems():
                    self.__log.debug('Fit %s model %s', fit, model)
                    target_conditions = {
                        run: {'t': np.array(res.index),
                              'y': np.array(res[fit['species']])}
                        for run, res in results.iteritems()}
                    fit_results[model][fitname] = self._fit(
                        target_conditions, '{}-{}'.format(
                            model, fitname),
                        fit, results_dir, n_p)
        return run_results, fit_results

    @staticmethod
    def set_results_dir(results_dir):
        if results_dir is None:
            results_dir = os.getcwd()
        return results_dir

    def _run_model(self, model, results_dir):
        '''
        Run simulations for the given model
        '''
        self.__log.info('Run %s model', model)
        model_settings = getattr(self, model)
        self.__log.debug('Model %s active %s', model,
                         model_settings['active'])
        if model_settings['active']:
            results = {}
            self.__log.debug('Run %s',
                             self.operating_conditions['runs'])
            for n in range(
                    self.operating_conditions['runs']):
                self.__log.debug(
                    'Initialize run %s for %s', n, model)
                run = globals()[model](
                    ultimate_analysis=self.ultimate_analysis,
                    proximate_analysis=self.proximate_analysis,
                    pressure=self.operating_conditions['pressure'],
                    name='{}-Run{}'.format(model, n)
                )
                run.path = results_dir
                self.__log.debug('Set path to: %s', run.path)
                # run.set_parameters(**getattr(self.reader, model))
                run.set_parameters(**model_settings)
                self.__log.debug('Set property run %s for %s', n,
                                 model)
                run.operating_conditions = (
                    self.operating_conditions['run{}'.format(n)])
                self.__log.debug('Run %s for %s', n, model)
                res = run.run()
                results['run{}'.format(n)] = res
                self.__log.debug('Finish run %s', results.keys())
        else:
            results = None
        return results

    def _fit(self, target_conditions, fitname, fit_settings,
             results_dir, n_p=1):
        '''
        Perform calibration fitting of the empirical model using
        results of the detailed model.

        Parameters
        ----------
        target_conditions: list
            List of target conditions for the calibration. Each entry
            of the list contains: 
            `{t: array, 'y': array, operating_conditions: array}`
            `t` and `y` time and volatile yield arrays of length
            N_points.
            operating_conditions: array (2, N_cond) containing the op.
            conditions.
        fit_settings: dict
            Dictionary containing settings for the evolution algorithm.
        results_dir: str
            Path where results are stored
        n_p: int
            Number of processors for the evolution
        '''
        model = fit_settings['model']
        self.__log.debug('Fit with model %s', model)
        parameters_min = fit_settings['parameters_min']
        parameters_max = fit_settings['parameters_max']
        parameters_init = fit_settings['parameters_init']
        method = fit_settings['method']
        fit_results = {}
        if method == 'evolve':
            npop = fit_settings['npop']
            ngen = fit_settings['ngen']
            mu = fit_settings['mu']
            lambda_ = fit_settings['lambda_']
            cxpb = fit_settings['cxpb']
            mutpb = fit_settings['mutpb']

            ga = pkp.evolution.Evolution(npop=npop, ngen=ngen,
                                         cxpb=cxpb, mutpb=mutpb,
                                         mu=mu, lambda_=lambda_)
            self.__log.debug('Init GA %s', ga)
            ga.empirical_model = getattr(pkp.empirical_model, model)
            self.__log.debug('Set GA model %s', ga.empirical_model)
            ga.parameters_range(parameters_min=parameters_min,
                                parameters_max=parameters_max)

            self.__log.debug('Set GA par range %s, %s',
                             ga._parameters_min, ga._parameters_max)

            [ga.set_target(
                t=res['t'], y=res['y'],
                operating_conditions=self.operating_conditions[run])
             for run, res in target_conditions.iteritems()]

            # self.__log.debug('Op. conditions %s',
            #                 ga.operating_conditions)

            ga.register()
            fit_results['best'] = ga.evolve(n_p=n_p, verbose=True)
            # run model and add to fit_results

            # plot results (evolution history)
            color = 'black'
            color_min = 'red'
            fig, ax = plt.subplots()
            fit_min, fit_max, fit_avg, fit_std = ga.log.select(
                'min', 'max', 'avg', 'std')
            ax.plot(fit_min, label='Min', color=color_min)
            ax.plot(fit_max, label='Max', color=color)
            ax.plot(fit_avg, label='Avg', color=color,
                    linestyle='dashed')
            ax.set_yscale('log')
            ax.legend(loc='best')
            ax.set_xlabel('N. generations')
            ax.set_ylabel('Fitness')
            ax.set_title(fitname)
            fig.savefig(os.path.join(results_dir,
                                     'evolution_{}.png'.format(fitname)))
            plt.close(fig)

            # plot yield
            self.__log.debug('Plot yields')
            fig, ax = plt.subplots()
            m = ga.empirical_model(fit_results['best'])
            det_model, fitname0 = fitname.split('-')
            for i, run in enumerate(sorted(target_conditions)):
                res = target_conditions[run]
                if i == 0:
                    l = '{} {}'.format(run, det_model)
                else:
                    l = run
                ax.plot(res['t'], res['y'], label=l, color=colors[i],
                        linestyle='solid')
                m.operating_conditions = self.operating_conditions[run]
                t_fit, y_fit = m.run(res['t'])
                if i == 0:
                    l = '{} {}'.format(run, m.__class__.__name__)
                else:
                    l = None
                ax.plot(t_fit, y_fit, color=colors[
                        i], linestyle='dashed', label=l)
            ax.set_ylabel('Yield')
            ax.set_xlabel('t, s')
            ax.legend(loc='best')
            ax.set_title(fitname)
            fig.savefig(os.path.join(results_dir,
                                     'yield_{}.png'.format(fitname)))
            plt.close(fig)

        else:
            raise NotImplementedError(
                'Fit method {} not implemented!'.format(method))
        return None
