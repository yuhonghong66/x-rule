import { ModelBase } from './index';

// Rule Model

export interface Condition {
  readonly feature: number;
  readonly category: number;
  // readonly support: number;
}

export interface Rule {
  readonly conditions: Condition[];
  readonly output: number[];
  // support: number[];
}

export interface RuleModel extends ModelBase {
  readonly type: 'rule';
  readonly rules: Rule[];
  readonly supports: number[][];
  // readonly discretizers: Discretizer[];
}

export function isRuleModel(model: ModelBase): model is RuleModel {
  return model.type === 'rule';
}

export class RuleList implements RuleModel {
  // public static readonly type: 'rule';
  public readonly name: string;
  public readonly type: 'rule';
  public readonly dataset: string;
  public readonly nFeatures: number;
  public readonly nClasses: number;
  public readonly rules: Rule[];
  public readonly target?: string;
  public supports: number[][];
  // public readonly discretizers: Discretizer[];
  // public data?: DataSet;

  constructor(raw: RuleModel) {
      const {dataset, nFeatures, nClasses, rules, target, name, supports} = raw;
      this.dataset = dataset;
      this.nFeatures = nFeatures;
      this.nClasses = nClasses;
      this.rules = rules;
      this.name = name;
      this.supports = supports;
      // this.supports = rules.map((r: Rule) => r.support);
      // this.discretizers = discretizers;
      this.type = 'rule';
      if (target) this.target = target;
  }

  public support(newSupport: number[][]): this {
      if (newSupport.length !== this.rules.length) {
          throw `Shape not match! newSupport has length ${newSupport.length}, but ${this.rules.length} is expected`;
      }
      this.supports = newSupport;
      // this.rules.forEach((r: Rule, i: number) => r.support = newSupport[i]);
      return this;
  }
  // public bindData(data: DataSet) {
  //     this.data = data;
  // }
}