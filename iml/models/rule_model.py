from typing import Optional, Dict, List
from math import inf
from functools import reduce

import numpy as np

from rpy2.robjects.packages import importr
from rpy2.robjects import FactorVector, DataFrame, numpy2ri, r

from mdlp.discretization import MDLP

from iml.models import Classifier, SurrogateMixin

numpy2ri.activate()

#utils = importr("utils")


def np2rdf(x, y=None, feature_names=None):
    x = np.array(x)
    if feature_names is None:
        feature_names = list(range(x.shape[1]))
    assert len(feature_names) == x.shape[1]

    fvs = [FactorVector(x[:, i]) for i in range(x.shape[1])]
    _dict = {feature_names[i]: fvs[i] for i in range(x.shape[1])}
    if y is not None:
        _dict['label'] = y
    return DataFrame(_dict)


class Rule:
    def __init__(self, feature_indices: List[int], categories: List[int], output: float, pos_support=None, neg_support=None):
        self.feature_indices = feature_indices
        self.categories = categories
        self.output = output
        self.pos_support = pos_support
        self.neg_support = neg_support

    def describe(self, feature_names=None, category_intervals=None, label=None, default=False):
        if label is None:
            label = "positive prob"
        output = "{}: {:.4f}".format(label, self.output)

        if feature_names is None:
            _feature_names = ["X" + str(idx) for idx in self.feature_indices]
        else:
            _feature_names = [feature_names[idx] for idx in self.feature_indices]
        if category_intervals is None:
            categories = [" = " + str(category) for category in self.categories]
        else:
            categories = []
            for interval in category_intervals:
                assert len(interval) == 2
                categories.append(" in " + str(interval))
        if default:
            s = "DEFAULT " + output
        else:
            s = "IF "
            # conditions
            s += " and ".join(["({}{})".format(feature, category) for feature, category in zip(_feature_names, categories)])
            # results
            s += " THEN " + output
        supports = []
        if self.pos_support:
            supports += ["+" + str(self.pos_support)]
        if self.neg_support:
            supports += ["-" + str(self.neg_support)]
        if len(supports) > 0:
            s += " [" + "/".join(supports) + "]"
        return s

    def is_satisfy(self, x_cat):
        satisfied = []
        if self.feature_indices[0] == -1 and len(self.feature_indices) == 1:
            return np.ones(x_cat.shape[0], dtype=bool)
        for idx, cat in zip(self.feature_indices, self.categories):
            satisfied.append(x_cat[:, idx] == cat)
        return reduce(np.logical_and, satisfied)


class SBRL(Classifier):
    """
    A binary classifier using R package sbrl
    """
    r_sbrl = importr('sbrl')

    def __init__(self, pos_sign='1', neg_sign='0', rule_minlen=1, rule_maxlen=2,
                 minsupport_pos=0.02, minsupport_neg=0.02, _lambda=50, eta=1, nchain=30, name='sbrl',
                 discretize=True, feature_names=None):
        super(SBRL, self).__init__(name)
        self._r_model = None
        self.options = {
            'pos_sign': pos_sign,
            'neg_sign': neg_sign,
            'rule_minlen': rule_minlen,
            'rule_maxlen': rule_maxlen,
            'minsupport_pos': minsupport_pos,
            'minsupport_neg': minsupport_neg,
            'lambda': _lambda,
            'eta': eta,
            'nchain': nchain,
        }
        self.discretizer = None
        if discretize:
            self.discretizer = MDLP()
        self._rule_indices = None  # type: Optional[np.ndarray]
        self._rule_probs = None  # type: Optional[np.ndarray]
        self._rule_names = None
        self._feature_names = feature_names
        self._mat_feature_rule = None
        self._rule_list = []  # type: List[Rule]

    @property
    def type(self):
        return 'sbrl'

    # def score(self, y_true, y_pred):
    #     return self.accuracy(y_true, y_pred)

    def train(self, x, y, feature_names=None, rediscretize: bool=True):
        """

        :param x: 2D np.ndarry (n_instances, n_features) could be continuous
        :param y: 1D np.ndarray (n_instances, ) labels
        :param feature_names:
        :param rediscretize: (bool) whether to use the new training data to fit the discretizer again
        :return:
        """
        _x = x
        if self.discretizer is not None:
            if rediscretize:
                print("discretizer re-fitted")
                self.discretizer.fit(x, y)
            _x = self.discretizer.transform(x)
        data = np2rdf(_x, y, feature_names)
        # print(x)
        # print(y)
        # Prevent printing from R
        r('sink("/dev/null")')
        self._r_model = self.r_sbrl.sbrl(data, **(self.options))
        self._rule_indices = numpy2ri.ri2py(self._r_model[0][0]).astype(int) - 1
        self._rule_probs = numpy2ri.ri2py(self._r_model[0][1])
        self._rule_names = numpy2ri.ri2py(self._r_model[1]).tolist()
        self._feature_names = numpy2ri.ri2py(self._r_model[2]).tolist()
        self._mat_feature_rule = numpy2ri.ri2py(self._r_model[3]).astype(np.bool)
        self._rule_list = []
        for i, idx in enumerate(self._rule_indices):
            _rule_name = self._rule_names[idx]
            # feature_indices, categories = self._rule_name2rule(_rule_name)
            self._rule_list.append(self._rule_name2rule(_rule_name, self._rule_probs[i]))
        acc, loss, support_summary = self.evaluate(x, y, stage='train')
        for rule, support in zip(self._rule_list, support_summary):
            rule.pos_support = support[0]
            rule.neg_support = support[1]

    def evaluate(self, x, y, stage='train'):
        acc = self.accuracy(y, self.predict(x))
        y_prob, support = self.predict_prob(x, rt_support=True)
        support_summary = []
        for i, _sup in enumerate(support):
            prob = self._rule_list[i].output
            pos = np.sum(y[_sup] == (1 if prob > 0.5 else 0))
            neg = np.sum(_sup) - pos
            support_summary.append((pos, neg))
        loss = self.log_loss(y, y_prob)
        prefix = 'Training'
        if stage == 'test':
            prefix = 'Testing'
        print(prefix + " accuracy: {:.5f}; loss: {:.5f}".format(acc, loss))
        return acc, loss, support_summary

    def _rule_name2rule(self, rule_name, prob):
        raw_rules = rule_name[1:-1].split(',')
        feature_indices = []
        categories = []
        for raw_rule in raw_rules:
            idx = raw_rule.find('=')
            if idx == '-1':
                raise ValueError("No '=' find in the rule!")
            feature_indices.append(int(raw_rule[1:idx]))
            categories.append(int(raw_rule[(idx+1):]))
        return Rule(feature_indices, categories, prob)

    def predict_prob(self, x, discretize=True, rt_support=False):
        """
            `X`  an instance of pandas.DataFrame object, representing the data to be making predictions on.
            `type`  whether the prediction is discrete or probabilistic.

            return a numpy.ndarray of shape (#datapoints, 2), the probability for each observations
        """
        _x = x
        if discretize and self.discretizer is not None:
            _x = self.discretizer.transform(x)

        # sbrl.predict
        # results = self.r_sbrl.predict_sbrl(self._r_model, np2rdf(x))
        # return np.array([numpy2ri.ri2py(result) for result in results]).T

        y = np.zeros([_x.shape[0], 2])
        un_satisfied = np.ones([_x.shape[0]], dtype=bool)
        support = []
        for rule in self._rule_list:
            satisfied = np.logical_and(rule.is_satisfy(_x), un_satisfied)
            y[satisfied, 1] = rule.output
            y[satisfied, 0] = 1 - rule.output
            # marking new satisfied instances as satisfied
            un_satisfied = np.logical_xor(satisfied, un_satisfied)
            if rt_support:
                support.append(satisfied)
        if rt_support:
            return y, support
        return y

    def predict(self, x, discretize=True):
        y_prob = self.predict_prob(x, discretize=discretize)
        # print(y_prob[:50])
        y_pred = np.argmax(y_prob, axis=1)
        return y_pred

    def describe2(self, feature_names=None, rt_str=False):
        n_rules = len(self._rule_indices)
        prefixes = ['IF']
        if n_rules > 2:
            prefixes += ['ELSE IF'] * (n_rules-2)
        # prefixes += ['ELSE (Default)']
        # if n_rules == 1:
        #     prefixes = ['Default']
        s = "The rule list is:\n\n"
        for i, prefix in enumerate(prefixes):
            idx = self._rule_indices[i]
            prob = self._rule_probs[i]
            rule_name = self._rule_names[idx]
            if feature_names is not None and self.discretizer is not None:
                rule_name = self.raw_rule2readable(rule_name, feature_names)
            s += "{0:<7} {1} (rule[{2}]) THEN positive prob: {3:.4f}\n\n".format(prefix, rule_name, idx, prob)
        if n_rules != 1:
            s += "ELSE "
        s += "DEFAULT (rule[-1]) positive prob: {:.4f}\n".format(self._rule_probs[-1])
        if rt_str:
            return s
        print(s)

    def describe(self, feature_names=None, rt_str=False):
        s = "The rule list is:\n\n     "

        n_rules = len(self._rule_indices)

        for i, rule in enumerate(self._rule_list):
            category_intervals = None
            if self.discretizer is not None:
                category_intervals = []
                for idx, cat in zip(rule.feature_indices, rule.categories):
                    category_intervals.append(
                        self.discretizer.cat2intervals(np.array([cat]), idx)[0]
                    )
            is_last = i == len(self._rule_list) - 1
            s += rule.describe(feature_names, category_intervals, label="positive prob", default=is_last) + "\n"
            if len(self._rule_list) > 1 and not is_last:
                s += "\nELSE "

        if rt_str:
            return s
        print(s)

    def raw_rule2readable(self, rule_name, feature_names):
        assert self.discretizer is not None
        raw_rules = rule_name[1:-1].split(',')
        readable_rules = []
        for raw_rule in raw_rules:
            idx = raw_rule.find('=')
            if idx == '-1':
                raise ValueError("No '=' find in the rule!")
            feature_index = int(raw_rule[1:idx])
            category = int(raw_rule[(idx+1):])
            interval = self.discretizer.cat2intervals(np.array([category]), feature_index)[0]
            readable_rules.append("(" + feature_names[feature_index] + " in " + str(interval) + ")")
        return " and ".join(readable_rules)


class RuleSurrogate(SBRL, SurrogateMixin):
    def __init__(self, **kwargs):
        # SurrogateMixin.__init__(self, name)
        SBRL.__init__(self, **kwargs)


# class MultiClassBRL(SBRL):



if __name__ == '__main__':
    print('test')
