import styled from "styled-components";
//import MachineLearning from "../../assets/img/machine-learning.png";
//import OperatingSystem from "../../assets/img/operating-system.png";
import {
  buttonStyles,
  colors,
  sizes,
  textStyles,
  breakpoints,
} from "../../../theme";

//const courseImages = {
//  "Machine learning": MachineLearning,
//  "Operating systems": OperatingSystem,
//};

export const MainContainer = styled.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 15px;
  padding: 16px;

  @media (min-width: ${breakpoints.tablet}) {
    flex-direction: row;
    flex-wrap: wrap;
    justify-content: center;
  }
`;

export const CourseCardContainer = styled.div`
  display: flex;
  width: ${sizes.cardWidth};
  flex-direction: column;
  border-radius: 12px;
  background: #fff;
  box-shadow: 2px 2px 10px rgba(0, 0, 0, 0.1);

  @media (max-width: ${breakpoints.tablet}) {
    width: 90%;
  }

  @media (min-width: ${breakpoints.desktop}) {
    width: ${sizes.cardWidth};
  }
`;

export const HeaderCard = styled.div`
  display: flex;
  padding: ${sizes.padding};
  flex-direction: column;
  align-items: center;
  gap: 6px;
  align-self: stretch;
`;

export const CourseTitle = styled.h3`
  ${textStyles}
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
  margin: 0;
`;

export const SemesterTitle = styled.p`
  ${textStyles}
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeMedium};
  font-weight: 400;
  margin: 0;
`;

export const ImageBlock = styled.div`
  width: ${sizes.cardWidth};
  height: ${sizes.cardHeight};
  background-image: ${(props) => `url(${props.$logo})`};
  background-size: cover;
  background-position: center;

  @media (max-width: ${breakpoints.tablet}) {
    width: 100%;
    height: 180px;
  }
`;

export const ButtonGroup = styled.div`
  display: flex;
  justify-content: space-between;
  padding: ${sizes.padding};
  flex-wrap: wrap;
  gap: 10px;

  @media (max-width: ${breakpoints.tablet}) {
    flex-direction: column;
    align-items: stretch;
  }
`;

export const Button = styled.button`
  ${buttonStyles}
  color: ${colors.buttonBorder};

  &:hover {
    border-color: ${colors.textPrimary};
    background: ${colors.buttonHover};
    color: ${colors.buttonText};
    cursor: pointer;
  }
`;

export const ButtonBack = styled.button`
  ${buttonStyles}
  color: ${colors.buttonBorder};
  border: none;
  background-color: #fff;

  &:hover {
    color: ${colors.cancel};
    cursor: pointer;
  }
`;

export const SelectButton = styled.button`
  ${buttonStyles}
  background: ${colors.buttonBackground};
  color: ${colors.buttonText};

  &:hover {
    border: 1px solid ${colors.buttonBorder};
    cursor: pointer;
  }
`;

export const DetailsContainer = styled.div`
  display: flex;
  width: 410px;
  padding: 16px;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;

  @media (max-width: ${breakpoints.tablet}) {
    width: 100%;
    padding: 12px;
  }
`;

export const InfoTitle = styled.h3`
  ${textStyles}
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
  margin: 0;
`;

export const Details = styled.div`
  display: flex;
  align-items: baseline;
  flex-direction: column;
`;

export const Title = styled.p`
  ${textStyles}
  color: ${colors.textPrimary};
  text-align: center;
  font-size: ${sizes.fontSizeMedium};
  font-weight: 400;
  margin: 0;
`;

export const Description = styled.p`
  ${textStyles}
  color: ${colors.textSecondary};
  text-align: center;
  font-size: ${sizes.fontSizeSmall};
  font-weight: 500;
  margin: 0;
`;

export const TextArea = styled.textarea`
  width: 415px;
  padding: 8px;
  margin-top: 16px;
  background-color: #f9f9f9;
  border: none;
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.05);
  ${textStyles}
  transition: border 0.3s, box-shadow 0.3s;

  &:focus {
    border-color: #007bff;
    box-shadow: 0 0 0 4px rgba(0, 123, 255, 0.1);
    outline: none;
    background-color: #fff;
  }

  @media (max-width: ${breakpoints.tablet}) {
    width: 100%;
  }
`;

export const AdminButtonWrapper = styled.div`
  display: flex;
  justify-content: flex-end;
  width: 100%;
  padding: 12px;

  @media (max-width: ${breakpoints.tablet}) {
    justify-content: center;
  }
`;

export const FixedAdminButton = styled.button`
  position: fixed;
  top: 60px;
  right: 20px;
  z-index: 3100;
  background-color: ${colors.save};
  color: white;
  border: none;
  border-radius: 8px;
  padding: 10px 16px;
  font-size: ${sizes.fontSizeMedium};
  font-weight: bold;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  transition: background-color 0.3s ease, transform 0.2s ease;

  &:hover {
    background-color: ${colors.buttonHover};
    transform: scale(1.05);
    cursor: pointer;
  }

  @media (max-width: ${breakpoints.tablet}) {
    padding: 8px 14px;
    font-size: ${sizes.fontSizeSmall};
    top: 50px;  /* для мобильных чуть повыше */
    right: 12px;
  }
`;

