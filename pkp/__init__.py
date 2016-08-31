'''
PKP Pyrolysis Kinetic Preprocessor
==================================
'''
from __future__ import division, absolute_import
from __future__ import print_function, unicode_literals

from autologging import logged
import ruamel_yaml as yaml
import os
import numpy as np
import pandas as pd

from pkp.cpd import CPD
from pkp.polimi import Polimi
from pkp.biopolimi import BioPolimi
import pkp.evolution

import matplotlib.pyplot as plt
try:
    plt.style.use('mystyle')
except:
    plt.style.use('ggplot')

colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
col_red = "#C54E6D"
col_green = "#009380"

models = ['CPD', 'Polimi', 'BioPolimi']


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
        run_results = {}
        fit_results = {}
        for model in self.models:
            model_settings = getattr(self, model)
            if model_settings['active']:
                self.__log.info('Run model %s', model)
                results = self._run_model(model=model,
                                          results_dir=results_dir)
                if results:
                    run_results[model] = results
                    self.__log.debug('Finish run %s %s', model,
                                     results.keys())
                else:
                    self.__log.warning('No results for %s', model)
                if model_settings['fit']:
                    self.__log.info('Start fit of %s model', model)
                    fit_results[model] = self._fit_model(
                        model, model_settings['fit'], n_p, results,
                        results_dir)
        return run_results, fit_results

    def _fit_model(self, model, model_settings, n_p, results,
                   results_dir):
        '''
        Run fitting of the given model

        Parameters
        ----------
        model: str, unicode
            Name of the detailed model to fit
        model_settings: dict
            Dictionary settings of the fitting
        n_p: int
            Number of processor for multiprocessing evolution
        results: dict
            Dictionary results of the detailed model
        results_dir: str, unicode
            Name of the output directory for storing results

        Returns
        -------
        fit_results: dict
            Contains results of fitting

        '''
        fit_results = {}
        for fitname, fit in model_settings.iteritems():
            self.__log.info('Fit %s model %s', fit, model)
            target_conditions = {
                run: {'t': np.array(res.index),
                      'y': np.array(res[fit['species']])}
                for run, res in results.iteritems()}
            fit_dict = {'model': model,
                        'fit': fitname,
                        'species': fit['species']}
            fit_results[fitname] = self._evolution(
                target_conditions, fit_dict,
                fit, results_dir, n_p)
            fit_results[fitname]['species'] = fit['species']
        return fit_results

    @staticmethod
    def set_results_dir(results_dir):
        if results_dir is None:
            results_dir = os.getcwd()
        return results_dir

    def _run_model(self, model, results_dir):
        '''
        Run simulations for the given model

        Parameters
        ----------
        model: str
            Name of the detailed model. Note that it should be the same
            of a defined class
        results_dir: str
            Path of results

        Returns
        -------
        results: dict
        '''
        model_settings = getattr(self, model)
        self.__log.debug('Model %s active %s', model,
                         model_settings['active'])
        if model_settings['active']:
            results = {}
            self.__log.debug('Run %s',
                             self.operating_conditions['runs'])
            vol_composition = pd.DataFrame()
            for n in range(
                    self.operating_conditions['runs']):
                res = self._run_single(model, model_settings, n,
                                       results_dir)
                results['run{}'.format(n)] = res

                # add last row to vol_composition
                vol_composition = vol_composition.append(
                    res.tail(1), ignore_index=True)
                self.__log.debug('Finish run %s', results.keys())

                # plot results
                self._plot_results(model, n, res, results_dir)

            # add index to vol_composition dataframe
            vol_composition.index = [
                'run{}'.format(n)
                for n in range(self.operating_conditions['runs'])]
            self.__log.debug('Export vol_composition to csv %s',
                             'finalyield_{}.csv'.format(model))
            vol_composition.to_csv(
                os.path.join(results_dir,
                             'finalyield_{}.csv'.format(model)),
                index=True)
        else:
            results = None
        return results

    def _run_single(self, model, model_settings, n, results_dir):
        '''
        Run a single simulation for the given detailed model.

        Parameters
        ----------
        model: str, unicode
            Model name
        model_settings: dict
            Settings of the given model
        n: int
            Run number
        results_dir: str, unicode
            Store results directory

        Returns
        -------
        res: pd.DataFrame
            Results datafram
        '''
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
        run.set_parameters(**model_settings)
        self.__log.debug('Set property run %s for %s', n,
                         model)
        run.operating_conditions = (
            self.operating_conditions['run{}'.format(n)])
        self.__log.debug('Run %s for %s', n, model)
        res = run.run()
        return res

    def _plot_results(self, model, n, res, results_dir):
        fig, ax = plt.subplots()
        for sp in ['tar', 'light_gas', 'char', 'solid',
                   'volatiles']:
            if sp in res:
                ax.plot(res.index, res[sp], label=sp)
        ax.set_xlabel('Time, s')
        ax.set_ylabel('Yield, daf')
        ax.legend(loc='best', frameon=False)
        ax.set_title('Run{} Model {}'.format(n, model))
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['bottom'].set_position(('outward', 20))
        ax.spines['left'].set_position(('outward', 20))
        ax.set_ylim([0, 1])
        # ax.spines['left'].set_color(col_right)
        # ax.spines['left'].set_color(col_right)
        ax1 = ax.twinx()
        ax1.plot(res.index, res['T'],
                 label='T', color=col_green)
        ax1.spines['top'].set_visible(False)
        ax1.spines['left'].set_visible(False)
        ax1.spines['bottom'].set_position(('outward', 20))
        ax1.spines['right'].set_position(('outward', 20))
        ax1.spines['right'].set_color(col_green)
        ax1.tick_params(axis='y', colors=col_green)
        ax1.set_ylabel('Other scale', color=col_green)
        ax1.set_ylabel('Temperature, K')
        ax1.set_ylim(
            [res['T'].min() - 100, res['T'].max() + 100])
        ax1.grid(False)
        fig.savefig(
            os.path.join(results_dir,
                         'yields_run{}_{}.png'.format(n,
                                                      model)),
            bbox_inches='tight')

    def _evolution(self, target_conditions, fit_dict, fit_settings,
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
        fit_dict: dict
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

            binary = True

            if binary:
                ga = pkp.evolution.EvolutionBinary(npop=npop, ngen=ngen,
                                                   cxpb=cxpb,
                                                   mutpb=mutpb,
                                                   mu=mu,
                                                   lambda_=lambda_)
            else:
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
            best = ga.evolve(n_p=n_p, verbose=True)

            fit_results['best'] = dict(
                zip(ga.empirical_model.parameters_names, best))
            self.__log.info('Best population: %s', fit_results['best'])

            fit_results['log'] = ga.log

            # run model and add to fit_results
            det_model, fitname = fit_dict['model'], fit_dict['fit']
            m = ga.empirical_model(fit_results['best'])
            emp_model = m.__class__.__name__
            self.__log.debug('Emp model %s', emp_model)
            filename = '{}_{}_{}'.format(fitname, det_model, emp_model)

            # plot results (evolution history)
            self._plot_evolution(det_model, filename, fitname, ga,
                                 results_dir)

            # plot yield
            self._plot_yieldfit(det_model, emp_model, filename,
                                fit_dict, fit_results, fitname, m,
                                results_dir, target_conditions)

        else:
            raise NotImplementedError(
                'Fit method {} not implemented!'.format(method))
        return fit_results

    def _plot_yieldfit(self, det_model, emp_model, filename, fit_dict,
                       fit_results, fitname, m, results_dir,
                       target_conditions):
        '''
        Plot comparison between the yields of the detailed model used as
        target and the yields obtained by the fitted empirical models

        Parameters
        ----------
        det_model
        emp_model
        filename
        fit_dict
        fit_results
        fitname
        m
        results_dir
        target_conditions

        Returns
        -------

        '''
        self.__log.debug('Plot yields')
        fig, ax = plt.subplots()
        runs = list(sorted(target_conditions))
        n_runs = len(runs)
        for i, run in enumerate(runs):
            fit_results[run] = {}
            res = target_conditions[run]
            if i == 0:
                l = '{} {}'.format(run, det_model)
            else:
                l = run
            self.__log.debug('Plot %s ', run)
            ax.plot(res['t'], res['y'], label=l, color=colors[i],
                    linestyle='solid')
            fit_results[run]['t'] = res['t']
            fit_results[run]['y'] = res['y']
            m.operating_conditions = self.operating_conditions[run]
            t_fit, y_fit = m.run(res['t'])
            if y_fit.ndim == 2:
                y_fit = y_fit[:, 0]
            fit_results[run]['y_fit'] = y_fit
            if i == 0:
                l = '{} {}'.format(run, m.__class__.__name__)
            else:
                l = None
            ax.plot(t_fit, y_fit, color=colors[
                i], linestyle='dashed', label=l)
        ax.set_ylabel('Yield {}'.format(fit_dict['species']))
        ax.set_xlabel('t, s')
        # add an extra legend
        # http://matplotlib.org/users/legend_guide.html#multiple-legend
        ax.add_artist(plt.legend(ax.lines[::2], runs,
                                 loc='upper right', frameon=False))
        ax.legend(ax.lines[:2],
                  [det_model, m.__class__.__name__],
                  loc='lower right', frameon=False)
        ax.set_title(
            'Fit {} from {} with {} ({})'.format(
                fit_dict['species'],
                det_model,
                emp_model,
                fitname))
        fig.savefig(os.path.join(results_dir,
                                 'yield_{}.png'.format(filename)),
                    bbox_inches='tight')
        plt.close(fig)

    def _plot_evolution(self, det_model, filename, fitname, ga,
                        results_dir):
        '''
        Plot the evolution history

        Parameters
        ----------
        det_model
        filename
        fitname
        ga
        results_dir

        Returns
        -------

        '''
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
        ax.set_title(
            'Fit {} with {}: fitness evolution ({})'.format(
                det_model, ga.empirical_model.__class__.__name__,
                fitname))
        fig.savefig(os.path.join(
            results_dir,
            'evolution_{}.png'.format(filename)))
        plt.close(fig)
