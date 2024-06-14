import {gql, useQuery} from '@apollo/client';
import {
  Alert,
  Box,
  Button,
  ButtonLink,
  Colors,
  ConfigEditorWithSchema,
  Dialog,
  DialogFooter,
  Group,
  Heading,
  Icon,
  Page,
  PageHeader,
  SplitPanelContainer,
  Subheading,
  Table,
  Tag,
} from '@dagster-io/ui-components';
import {
  ConfigSchema,
  ConfigSchema_allConfigTypes,
  ConfigSchema_allConfigTypes_CompositeConfigType_fields,
} from '@dagster-io/ui-components/src/components/configeditor/types/ConfigSchema';
import React, {useState} from 'react';
import {useParams} from 'react-router-dom';
import {v4 as uuidv4} from 'uuid';

import {
  BlueprintManagerRootQuery,
  BlueprintManagerRootQueryVariables,
} from './types/BlueprintManagerRoot.types';
import {showCustomAlert} from '../app/CustomAlertProvider';
import {PYTHON_ERROR_FRAGMENT} from '../app/PythonErrorFragment';
import {useTrackPageView} from '../app/analytics';
import {Blueprint, BlueprintKey} from '../graphql/types';
import {useDocumentTitle} from '../hooks/useDocumentTitle';
import {RepositoryLink} from '../nav/RepositoryLink';
import {Loading} from '../ui/Loading';
import {repoAddressToSelector} from '../workspace/repoAddressToSelector';
import {RepoAddress} from '../workspace/types';

type JsonSchema = {
  type: string;
  properties: {
    [key: string]: JsonSchema;
  };
  description?: string;
  required?: string[];
};

const jsonSchemaToConfigSchemaInner = (
  jsonSchema: JsonSchema,
): {
  types: ConfigSchema_allConfigTypes[];
  rootKey: string;
} => {
  console.log(jsonSchema);
  if (jsonSchema.type === 'object') {
    const fieldTypesByKey = Object.entries(jsonSchema.properties).reduce(
      (accum, [key, value]) => {
        accum[key] = jsonSchemaToConfigSchemaInner(value);
        return accum;
      },
      {} as Record<string, {types: ConfigSchema_allConfigTypes[]; rootKey: string}>,
    );

    const fieldEntries = Object.entries(jsonSchema.properties).map(([key, value]) => {
      return {
        __typename: 'ConfigTypeField',
        name: key,
        description: value.description,
        isRequired: (jsonSchema.required || []).includes(key),
        configTypeKey: fieldTypesByKey[key]?.rootKey,
        defaultValueAsJson: null,
      };
    }) as ConfigSchema_allConfigTypes_CompositeConfigType_fields[];

    // root key (generate a random uuid)
    const rootKeyUuid = uuidv4();

    return {
      types: [
        {
          __typename: 'CompositeConfigType',
          key: rootKeyUuid,
          description: jsonSchema.description || null,
          isSelector: false,
          typeParamKeys: [],
          fields: fieldEntries,
        },
        ...Object.values(fieldTypesByKey).flatMap((x) => x.types),
      ],
      rootKey: rootKeyUuid,
    };
  } else {
    const rootKeyUuid = uuidv4();

    return {
      types: [
        {
          __typename: 'RegularConfigType',
          givenName: 'Any',
          key: rootKeyUuid,
          description: '',
          isSelector: false,
          typeParamKeys: [],
        },
      ],
      rootKey: rootKeyUuid,
    };
  }
};

const jsonSchemaToConfigSchema = (jsonSchema: any): ConfigSchema => {
  // get first object in jsonSchema.definitions
  const firstKey = Object.keys(jsonSchema.definitions)[0];
  const firstValue = jsonSchema.definitions[firstKey];
  const {types, rootKey} = jsonSchemaToConfigSchemaInner(firstValue);

  return {
    __typename: 'ConfigSchema',
    rootConfigType: {
      __typename: 'CompositeConfigType',
      key: rootKey,
    },
    allConfigTypes: types,
  };
};

interface Props {
  repoAddress: RepoAddress;
}

const EditBlueprintDialog = ({
  isOpen,
  onClose,
  configSchema,
  config,
  setConfig,
  title,
  onSave,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSave: () => void;
  configSchema: ConfigSchema | null;
  config: string;
  setConfig: (config: string) => void;
  title: string;
}) => {
  return (
    <Dialog
      isOpen={isOpen}
      title={title}
      onClose={onClose}
      style={{maxWidth: '90%', minWidth: '70%', width: 1000}}
    >
      <ConfigEditorWithSchema
        onConfigChange={setConfig}
        config={config}
        configSchema={configSchema}
        isLoading={false}
        identifier="foo"
      />
      <DialogFooter topBorder>
        <Button intent="none" onClick={onClose}>
          Cancel
        </Button>
        <Button intent="primary" onClick={onSave}>
          Save
        </Button>
      </DialogFooter>
    </Dialog>
  );
};

export const BlueprintManagerRoot = (props: Props) => {
  useTrackPageView();

  const {repoAddress} = props;
  const {blueprintManagerName} = useParams<{blueprintManagerName: string}>();

  const [config, setConfig] = useState<string>('');
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editorTarget, setEditorTarget] = useState<BlueprintKey | null>(null);

  useDocumentTitle(`Blueprint Manager: ${blueprintManagerName}`);

  const blueprintManagerSelector = {
    ...repoAddressToSelector(repoAddress),
    blueprintManagerName,
  };
  const queryResult = useQuery<BlueprintManagerRootQuery, BlueprintManagerRootQueryVariables>(
    BLUEPRINT_MANAGER_ROOT_QUERY,
    {
      variables: {
        blueprintManagerSelector,
      },
    },
  );

  const blueprintManager =
    queryResult.data?.blueprintManagerOrError.__typename === 'BlueprintManager'
      ? queryResult.data.blueprintManagerOrError
      : null;

  const jsonSchemaConfigSchema = React.useMemo(() => {
    if (!blueprintManager?.schema) {
      return null;
    }

    const jsonSchema = JSON.parse(blueprintManager.schema.schema);
    return jsonSchemaToConfigSchema(jsonSchema);
  }, [blueprintManager?.schema]);

  const openEditorForBlueprint = (blueprint: Blueprint) => {
    setConfig(JSON.parse(blueprint.blob.value));
    setEditorTarget(blueprint.key);
    setIsEditorOpen(true);
  };

  const openEditorForNewBlueprint = () => {
    setConfig('');
    setEditorTarget(null);
    setIsEditorOpen(true);
  };

  const saveConfig = () => {
    setIsEditorOpen(false);
  };

  return (
    <Page style={{height: '100%', overflow: 'hidden'}}>
      <PageHeader
        title={<Heading>{blueprintManager?.name}</Heading>}
        tags={
          <Tag icon="add_circle">
            BlueprintManager in <RepositoryLink repoAddress={repoAddress} />
          </Tag>
        }
      />
      <Loading queryResult={queryResult} allowStaleData={true}>
        {({blueprintManagerOrError}) => {
          if (blueprintManagerOrError.__typename !== 'BlueprintManager') {
            let message: string | null = null;
            if (blueprintManagerOrError.__typename === 'PythonError') {
              message = blueprintManagerOrError.message;
            }

            return (
              <Alert
                intent="warning"
                title={
                  <Group direction="row" spacing={4}>
                    <div>Could not load blueprintmanager.</div>
                    {message && (
                      <ButtonLink
                        color={Colors.linkDefault()}
                        underline="always"
                        onClick={() => {
                          showCustomAlert({
                            title: 'Python error',
                            body: message,
                          });
                        }}
                      >
                        View error
                      </ButtonLink>
                    )}
                  </Group>
                }
              />
            );
          }

          return (
            <div style={{height: '100%', display: 'flex'}}>
              <SplitPanelContainer
                identifier="blueprint-manager-details"
                firstInitialPercent={50}
                firstMinSize={400}
                first={
                  <Box>
                    <Box
                      flex={{
                        direction: 'row',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                      padding={{vertical: 12, horizontal: 16}}
                    >
                      <Subheading>{blueprintManager?.blueprints.length} Blueprints</Subheading>
                      <Button
                        intent="primary"
                        icon={<Icon name="add" />}
                        onClick={() => openEditorForNewBlueprint()}
                      >
                        Create new Blueprint
                      </Button>
                    </Box>
                    <Table>
                      <thead>
                        <tr>
                          <th>Blueprint Name</th>
                        </tr>
                      </thead>
                      <tbody>
                        {blueprintManager?.blueprints.map((blueprint) => {
                          return (
                            <tr key={blueprint.id}>
                              <td>
                                <Box flex={{direction: 'row', justifyContent: 'space-between'}}>
                                  <Box
                                    flex={{direction: 'row', alignItems: 'center', gap: 4}}
                                    style={{maxWidth: '100%'}}
                                  >
                                    <Icon name="add_circle" color={Colors.accentBlue()} />
                                    <div
                                      style={{
                                        maxWidth: '100%',
                                        whiteSpace: 'nowrap',
                                        fontWeight: 500,
                                      }}
                                    >
                                      {blueprint.key.identifierWithinManager}
                                    </div>
                                  </Box>
                                  <Button
                                    icon={<Icon name="edit" />}
                                    onClick={() => openEditorForBlueprint(blueprint)}
                                  />
                                </Box>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </Table>
                    <EditBlueprintDialog
                      isOpen={isEditorOpen}
                      onClose={() => setIsEditorOpen(false)}
                      configSchema={jsonSchemaConfigSchema}
                      config={config}
                      setConfig={setConfig}
                      title={
                        editorTarget
                          ? `Edit Blueprint ${editorTarget?.identifierWithinManager}`
                          : 'Create new Blueprint'
                      }
                      onSave={saveConfig}
                    />
                  </Box>
                }
                second={
                  <Box padding={{bottom: 48}} style={{overflowY: 'auto'}}>
                    <Box
                      flex={{gap: 4, direction: 'column'}}
                      margin={{left: 24, right: 12, vertical: 16}}
                    >
                      <Heading>{blueprintManager?.name}</Heading>
                    </Box>
                  </Box>
                }
              />
            </div>
          );
        }}
      </Loading>
    </Page>
  );
};

const BLUEPRINT_MANAGER_ROOT_QUERY = gql`
  query BlueprintManagerRootQuery($blueprintManagerSelector: BlueprintManagerSelector!) {
    blueprintManagerOrError(blueprintManagerSelector: $blueprintManagerSelector) {
      __typename
      ... on BlueprintManager {
        id
        name
        schema {
          schema
        }
        blueprints {
          id
          key {
            managerName
            identifierWithinManager
          }
          blob {
            value
          }
        }
      }
      ...PythonErrorFragment
    }
  }
  ${PYTHON_ERROR_FRAGMENT}
`;
